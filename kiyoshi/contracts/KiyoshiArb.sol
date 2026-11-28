// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

// KiyoshiArb: Textile-first arbitrage in ONE transaction, owned by the Kiyoshi bot wallet.
//
// Why (docs/earnings-investigation.pdf): since 13 Nov, arbitrage contracts fill Textile quotes and sell on Uniswap in a
// single transaction. Our two-transaction trades lost ~4 bps between the legs and Textile-first trades fell to 34%
// profitable. Here both legs run in one transaction that reverts unless it ends with at least `minProfit` more USDT
// than it started with, so a losing trade cannot happen: a failed try costs only gas.
//
// How Textile settles (docs.textilecredit.com/protocol/settlement): a firm quote is a set of maker orders signed for
// Textile's UniswapX LimitOrderReactor and bound to one taker. The taker calls the reactor (`executeBatch`); the
// reactor sends the makers' tokens to the caller and pulls the caller's payment with transferFrom. This contract is
// the taker: Textile verifies its control through EIP-1271 (isValidSignature below).
//
// Safety:
//   - Only the owner (the bot wallet, fixed at deployment) can trade or withdraw, and withdrawals go only to the owner.
//   - Allowances exist only inside a trade (to the reactor for the quote's payment, to the router for the sale) and
//     are set back to zero before it ends. The contract never approves Permit2 or anything else.
//   - isValidSignature accepts only a signature by the owner over keccak256("KiyoshiArb", this contract, chainId,
//     hash), so no other signature the bot wallet has ever made (heartbeats, Textile proofs for the wallet itself)
//     can be replayed as this contract's.
//   - The reactor call must be executeBatch or execute (the two calls Textile returns), to the fixed reactor.
//   - `paused` (owner only) blocks trading; withdraw always works.

interface IERC20 {
    function balanceOf(address) external view returns (uint256);
    function approve(address, uint256) external returns (bool);
    function transfer(address, uint256) external returns (bool);
}

interface ISwapRouter02 {
    struct ExactInputSingleParams {
        address tokenIn;
        address tokenOut;
        uint24 fee;
        address recipient;
        uint256 amountIn;
        uint256 amountOutMinimum;
        uint160 sqrtPriceLimitX96;
    }
    function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut);
}

contract KiyoshiArb {
    address public immutable owner;
    address public immutable reactor;
    address public immutable router;
    address public immutable usdt;
    bool public paused;

    bytes4 private constant EXECUTE = 0x3f62192e; // execute((bytes,bytes))
    bytes4 private constant EXECUTE_BATCH = 0x0d7a16c3; // executeBatch((bytes,bytes)[])
    bytes4 private constant MAGIC = 0x1626ba7e; // EIP-1271

    event Trade(address indexed token, uint256 usdtIn, uint256 tokenGot, uint256 usdtOut, uint256 profit);
    event Paused(bool paused);

    error NotOwner();
    error IsPaused();
    error BadCall();
    error NoProfit(uint256 usdtBefore, uint256 usdtAfter);
    error TransferFailed();

    modifier onlyOwner() {
        if (msg.sender != owner) revert NotOwner();
        _;
    }

    constructor(address owner_, address reactor_, address router_, address usdt_) {
        owner = owner_;
        reactor = reactor_;
        router = router_;
        usdt = usdt_;
    }

    /// Buy `token` with USDT through a Textile firm quote (its `transactions.swap.data`), sell all of it on Uniswap
    /// (v3 pool with `fee`), and revert unless USDT grew by at least `minProfit`. `takerPays` is the quote's USDT debit.
    function textileThenUniswap(bytes calldata textileCall, uint256 takerPays, address token, uint24 fee, uint256 minProfit)
        external
        onlyOwner
        returns (uint256 profit)
    {
        if (paused) revert IsPaused();
        if (textileCall.length < 4) revert BadCall();
        bytes4 sel = bytes4(textileCall[:4]);
        if (sel != EXECUTE && sel != EXECUTE_BATCH) revert BadCall();
        if (token == usdt) revert BadCall();

        uint256 usdtBefore = IERC20(usdt).balanceOf(address(this));
        uint256 tokenBefore = IERC20(token).balanceOf(address(this));

        _approve(usdt, reactor, takerPays);
        (bool ok, bytes memory ret) = reactor.call(textileCall);
        if (!ok) _bubble(ret);
        _approve(usdt, reactor, 0);
        uint256 paid = usdtBefore - IERC20(usdt).balanceOf(address(this));

        uint256 got = IERC20(token).balanceOf(address(this)) - tokenBefore;
        _approve(token, router, got);
        ISwapRouter02(router).exactInputSingle(
            ISwapRouter02.ExactInputSingleParams({
                tokenIn: token, tokenOut: usdt, fee: fee, recipient: address(this),
                amountIn: got, amountOutMinimum: 0, sqrtPriceLimitX96: 0
            })
        );
        _approve(token, router, 0);

        uint256 usdtAfter = IERC20(usdt).balanceOf(address(this));
        if (usdtAfter < usdtBefore + minProfit) revert NoProfit(usdtBefore, usdtAfter);
        profit = usdtAfter - usdtBefore;
        emit Trade(token, paid, got, usdtAfter - (usdtBefore - paid), profit);
    }

    /// Send `amount` of `token` (type(uint256).max = all of it) back to the owner. Always allowed, even when paused.
    function withdraw(address token, uint256 amount) external onlyOwner {
        uint256 bal = IERC20(token).balanceOf(address(this));
        _transfer(token, owner, amount > bal ? bal : amount);
    }

    function setPaused(bool p) external onlyOwner {
        paused = p;
        emit Paused(p);
    }

    /// EIP-1271: valid only for the owner's signature over this contract's own wrapping of `hash`.
    function isValidSignature(bytes32 hash, bytes calldata signature) external view returns (bytes4) {
        if (signature.length != 65) return 0xffffffff;
        bytes32 wrapped = keccak256(abi.encode(keccak256("KiyoshiArb"), address(this), block.chainid, hash));
        bytes32 r = bytes32(signature[0:32]);
        bytes32 s = bytes32(signature[32:64]);
        uint8 v = uint8(signature[64]);
        if (uint256(s) > 0x7FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF5D576E7357A4501DDFE92F46681B20A0) return 0xffffffff;
        address signer = ecrecover(wrapped, v, r, s);
        return signer != address(0) && signer == owner ? MAGIC : bytes4(0xffffffff);
    }

    // USDT-style tokens may return nothing and may reject approve(nonzero -> nonzero): reset to zero first.
    function _approve(address token, address spender, uint256 amount) private {
        _call(token, abi.encodeWithSelector(IERC20.approve.selector, spender, 0));
        if (amount > 0) _call(token, abi.encodeWithSelector(IERC20.approve.selector, spender, amount));
    }

    function _transfer(address token, address to, uint256 amount) private {
        _call(token, abi.encodeWithSelector(IERC20.transfer.selector, to, amount));
    }

    function _call(address token, bytes memory data) private {
        (bool ok, bytes memory ret) = token.call(data);
        if (!ok || (ret.length > 0 && !abi.decode(ret, (bool)))) revert TransferFailed();
    }

    function _bubble(bytes memory ret) private pure {
        assembly {
            revert(add(ret, 32), mload(ret))
        }
    }
}
