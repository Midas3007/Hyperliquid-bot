// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.28;

import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

/// @title Midas × Kiyoshi Fund vault (Phase 1)
/// @notice Members deposit USDT and receive fund shares; they redeem shares for USDT. Requests are queued and
/// settled together (once a day) at the fund value the operator reports, so nobody can time the bot.
/// The money is traded from the operator's wallet (the bot); this contract keeps the share ledger, each member's
/// cost, the performance fee and the queues, and only ever pays redemptions to the member who owns the shares.
///
/// Roles: owner (pause, assign roles, all behind a 48-hour delay except pause), operator (reports value, settles,
/// receives deposits, funds redemptions), manager (fee rate behind the delay and a hard cap, allow-list, limits),
/// fee recipient (receives fee shares, pays no fee itself).
///
/// Shares cannot be transferred between wallets in this version (transfers come in Phase 2 with their own fee).
contract FundVault is ERC20, ReentrancyGuard {
    using SafeERC20 for IERC20;

    // ---------- constants ----------
    uint256 public constant DELAY = 48 hours;
    uint256 public constant MAX_FEE_BPS = 2000; // the manager can never set the fee above 20% of profit
    uint256 public constant MAX_QUEUE = 100; // pending requests per settlement, keeps settle() within gas
    uint256 private constant SCALE = 1e12; // 1 share (18 decimals) = 1 USDT (6 decimals) at $1.00

    IERC20 public immutable asset;

    // ---------- roles and settings ----------
    address public owner;
    address public operator;
    address public manager;
    address public feeRecipient;
    bool public paused;
    uint256 public feeBps = 1000; // 10% of profit
    uint256 public maxNavChangeBps = 5000; // the reported value may move at most 50% a day (bugs, bad prices)
    uint256 public depositCap; // max fund value after deposits, in USDT units (6 decimals); 0 = closed
    uint256 public minDeposit = 1e6; // 1 USDT
    mapping(address => bool) public allowed;

    // ---------- fund state ----------
    uint256 public nav; // fund value (USDT units) as last reported, excluding queued deposits and unpaid payouts
    uint256 public navUpdatedAt;
    uint256 public anchorNav; // the value the daily limit is measured from
    uint256 public anchorAt;
    uint256 public settlements;
    mapping(address => uint256) public cost; // USDT a member has put in for the shares they hold

    // ---------- queues ----------
    mapping(address => uint256) public pendingDeposit; // USDT waiting for the next settlement
    mapping(address => uint256) public pendingRedeem; // shares held in escrow for the next settlement
    mapping(address => uint256) public claimable; // payouts that could not be pushed (claim() pays them)
    uint256 public totalPendingDeposits;
    uint256 public totalClaimable;
    address[] private depositQueue;
    address[] private redeemQueue;
    mapping(address => bool) private inDepositQueue;
    mapping(address => bool) private inRedeemQueue;

    // ---------- delayed changes ----------
    enum Change { Owner, Operator, Manager, FeeRecipient, FeeBps, MaxNavChange }
    struct Pending { uint256 value; uint256 eta; }
    mapping(Change => Pending) public pending;

    // ---------- events ----------
    event DepositRequested(address indexed member, uint256 assets);
    event DepositCancelled(address indexed member, uint256 assets);
    event RedeemRequested(address indexed member, uint256 shares);
    event RedeemCancelled(address indexed member, uint256 shares);
    event Deposited(address indexed member, uint256 assets, uint256 shares, uint256 settlement);
    event Redeemed(address indexed member, uint256 shares, uint256 gross, uint256 fee, uint256 paid, uint256 settlement);
    event Claimed(address indexed member, uint256 assets);
    event ValueReported(uint256 nav, uint256 supply, uint256 at);
    event Settled(uint256 indexed settlement, uint256 nav, uint256 supply, uint256 deposits, uint256 payouts);
    event OpeningShares(address indexed to, uint256 assets, uint256 shares);
    event ChangeQueued(Change indexed change, uint256 value, uint256 eta);
    event ChangeCancelled(Change indexed change);
    event ChangeExecuted(Change indexed change, uint256 value);
    event Paused(bool paused);
    event Allowed(address indexed member, bool allowed);
    event LimitsSet(uint256 depositCap, uint256 minDeposit);

    error NotAuthorized();
    error IsPaused();
    error NotAllowed();
    error BadAmount();
    error QueueFull();
    error TooEarly();
    error NothingQueued();
    error ValueJump();
    error CapReached();
    error NonTransferable();
    error AlreadyOpened();

    modifier only(address who) {
        if (msg.sender != who) revert NotAuthorized();
        _;
    }

    modifier onlyManagers() {
        if (msg.sender != manager && msg.sender != owner) revert NotAuthorized();
        _;
    }

    modifier notPaused() {
        if (paused) revert IsPaused();
        _;
    }

    constructor(IERC20 asset_, address owner_, address operator_, address manager_, address feeRecipient_)
        ERC20("Midas Kiyoshi Fund Share", "MKF")
    {
        if (owner_ == address(0) || operator_ == address(0) || manager_ == address(0) || feeRecipient_ == address(0)) {
            revert BadAmount();
        }
        asset = asset_;
        owner = owner_;
        operator = operator_;
        manager = manager_;
        feeRecipient = feeRecipient_;
    }

    // =====================================================================================================
    // Members
    // =====================================================================================================

    /// @notice Queue a deposit. The USDT waits in this contract until the next settlement, where it buys shares
    /// at that day's price. It can be cancelled (refunded) until then.
    function requestDeposit(uint256 assets) external nonReentrant notPaused {
        if (!allowed[msg.sender]) revert NotAllowed();
        if (assets < minDeposit) revert BadAmount();
        if (nav + totalPendingDeposits + assets > depositCap) revert CapReached();
        if (!inDepositQueue[msg.sender]) {
            if (depositQueue.length >= MAX_QUEUE) revert QueueFull();
            inDepositQueue[msg.sender] = true;
            depositQueue.push(msg.sender);
        }
        asset.safeTransferFrom(msg.sender, address(this), assets);
        pendingDeposit[msg.sender] += assets;
        totalPendingDeposits += assets;
        emit DepositRequested(msg.sender, assets);
    }

    /// @notice Take back a queued deposit before it settles. Works while paused.
    function cancelDeposit() external nonReentrant {
        uint256 assets = pendingDeposit[msg.sender];
        if (assets == 0) revert NothingQueued();
        pendingDeposit[msg.sender] = 0;
        totalPendingDeposits -= assets;
        asset.safeTransfer(msg.sender, assets);
        emit DepositCancelled(msg.sender, assets);
    }

    /// @notice Queue shares for redemption at the next settlement. The shares move into escrow here; the USDT
    /// (after the performance fee on this member's profit) is paid to the member at settlement.
    function requestRedeem(uint256 shares) external nonReentrant notPaused {
        if (shares == 0 || shares > balanceOf(msg.sender)) revert BadAmount();
        if (!inRedeemQueue[msg.sender]) {
            if (redeemQueue.length >= MAX_QUEUE) revert QueueFull();
            inRedeemQueue[msg.sender] = true;
            redeemQueue.push(msg.sender);
        }
        _update(msg.sender, address(this), shares);
        pendingRedeem[msg.sender] += shares;
        emit RedeemRequested(msg.sender, shares);
    }

    /// @notice Take queued shares back out of escrow before they settle. Works while paused.
    function cancelRedeem() external nonReentrant {
        uint256 shares = pendingRedeem[msg.sender];
        if (shares == 0) revert NothingQueued();
        pendingRedeem[msg.sender] = 0;
        _update(address(this), msg.sender, shares);
        emit RedeemCancelled(msg.sender, shares);
    }

    /// @notice Collect a payout that could not be sent automatically at settlement. Works while paused.
    function claim() external nonReentrant {
        uint256 assets = claimable[msg.sender];
        if (assets == 0) revert NothingQueued();
        claimable[msg.sender] = 0;
        totalClaimable -= assets;
        asset.safeTransfer(msg.sender, assets);
        emit Claimed(msg.sender, assets);
    }

    // =====================================================================================================
    // Operator (the bot wallet)
    // =====================================================================================================

    /// @notice Publish the fund value between settlements (for the dashboard's share price).
    function reportValue(uint256 newNav) external only(operator) notPaused {
        _checkJump(newNav);
        nav = newNav;
        navUpdatedAt = block.timestamp;
        emit ValueReported(newNav, totalSupply(), block.timestamp);
    }

    /// @notice Settle every queued redemption and deposit at one price: newNav / total shares.
    /// newNav is the fund's value held by the operator (all of the bot wallet's tokens, priced in USDT), without
    /// queued deposits or unpaid payouts that sit in this contract. Redemptions are paid first, then deposits buy
    /// shares. If deposits do not cover the payouts, the difference is pulled from the operator (approve first);
    /// any surplus deposit money is sent to the operator to trade.
    function settle(uint256 newNav) external nonReentrant only(operator) notPaused {
        uint256 supply = totalSupply();
        if (supply > 0) _checkJump(newNav);
        else if (newNav != 0) revert ValueJump(); // an empty fund holds nothing
        uint256 id = ++settlements;

        (address[] memory payees, uint256[] memory amounts, uint256 payouts) = _settleRedeems(newNav, supply, id);
        uint256 deposits = _settleDeposits(newNav, supply, id);

        // Cash: deposits pay the redemptions first; the operator tops up or receives the difference.
        if (payouts > deposits) asset.safeTransferFrom(operator, address(this), payouts - deposits);
        else if (deposits > payouts) asset.safeTransfer(operator, deposits - payouts);

        for (uint256 i; i < payees.length; ++i) {
            if (amounts[i] == 0) continue;
            if (!_tryPay(payees[i], amounts[i])) {
                claimable[payees[i]] += amounts[i];
                totalClaimable += amounts[i];
            }
        }

        nav = newNav + deposits - payouts;
        navUpdatedAt = block.timestamp;
        // money in and out is not a price move: shift the daily anchor by it
        anchorNav = anchorNav + deposits > payouts ? anchorNav + deposits - payouts : 0;
        if (supply == 0) (anchorNav, anchorAt) = (nav, block.timestamp);
        emit Settled(id, nav, totalSupply(), deposits, payouts);
    }

    /// Redemptions at newNav / supply. Each member pays the fee on their own profit (value of the shares minus
    /// the part of their cost those shares carry); the fee stays in the fund as the fee recipient's shares.
    function _settleRedeems(uint256 newNav, uint256 supply, uint256 id)
        private
        returns (address[] memory payees, uint256[] memory amounts, uint256 payouts)
    {
        uint256 n = redeemQueue.length;
        payees = new address[](n);
        amounts = new uint256[](n);
        uint256 burned;
        uint256 feeShares;
        for (uint256 i; i < n; ++i) {
            address m = redeemQueue[i];
            inRedeemQueue[m] = false;
            uint256 shares = pendingRedeem[m];
            if (shares == 0) continue;
            pendingRedeem[m] = 0;
            (uint256 paid, uint256 fs) = _redeemOne(m, shares, newNav, supply, id);
            feeShares += fs;
            burned += shares - fs;
            payouts += paid;
            payees[i] = m;
            amounts[i] = paid;
        }
        delete redeemQueue;
        if (burned > 0) _burn(address(this), burned);
        if (feeShares > 0) _update(address(this), feeRecipient, feeShares);
    }

    function _redeemOne(address m, uint256 shares, uint256 newNav, uint256 supply, uint256 id)
        private
        returns (uint256 paid, uint256 fs)
    {
        uint256 gross = shares * newNav / supply;
        uint256 costPart = cost[m] * shares / (balanceOf(m) + shares);
        cost[m] -= costPart;
        uint256 fee;
        if (m != feeRecipient && gross > costPart) fee = (gross - costPart) * feeBps / 10_000;
        fs = fee * supply / newNav;
        if (fs > shares) fs = shares;
        paid = gross - fee;
        emit Redeemed(m, shares, gross, fee, paid, id);
    }

    /// Deposits buy shares at the same price as the redemptions: newNav / supply (before this settlement).
    function _settleDeposits(uint256 newNav, uint256 supply, uint256 id) private returns (uint256 deposits) {
        uint256 n = depositQueue.length;
        for (uint256 i; i < n; ++i) {
            address m = depositQueue[i];
            inDepositQueue[m] = false;
            uint256 assets = pendingDeposit[m];
            if (assets == 0) continue;
            pendingDeposit[m] = 0;
            uint256 shares = supply == 0 ? assets * SCALE : assets * supply / newNav;
            if (shares == 0) revert BadAmount();
            _mint(m, shares);
            cost[m] += assets;
            deposits += assets;
            emit Deposited(m, assets, shares, id);
        }
        delete depositQueue;
        totalPendingDeposits -= deposits;
    }

    // =====================================================================================================
    // Owner (the bot wallet): pause, opening shares, roles (delayed)
    // =====================================================================================================

    function setPaused(bool p) external only(owner) {
        paused = p;
        emit Paused(p);
    }

    /// @notice One time, before anyone else holds shares: mint the opening position at $1.00 per share.
    /// assets must equal the fund value the operator holds at that moment.
    function openingShares(address to, uint256 assets) external only(owner) {
        if (totalSupply() != 0 || settlements != 0) revert AlreadyOpened();
        if (assets == 0 || to == address(0)) revert BadAmount();
        uint256 shares = assets * SCALE;
        _mint(to, shares);
        cost[to] += assets;
        nav = assets;
        navUpdatedAt = block.timestamp;
        (anchorNav, anchorAt) = (assets, block.timestamp);
        emit OpeningShares(to, assets, shares);
        emit ValueReported(assets, shares, block.timestamp);
    }

    /// @notice Queue a delayed change. Roles and the value-jump limit: owner. Fee rate: manager.
    function queueChange(Change c, uint256 value) external {
        if (c == Change.FeeBps) {
            if (msg.sender != manager) revert NotAuthorized();
            if (value > MAX_FEE_BPS) revert BadAmount();
        } else {
            if (msg.sender != owner) revert NotAuthorized();
            if (c == Change.MaxNavChange) {
                if (value < 100 || value > 50_000) revert BadAmount();
            } else if (value == 0 || value >> 160 != 0) {
                revert BadAmount(); // a non-zero address
            }
        }
        uint256 eta = block.timestamp + DELAY;
        pending[c] = Pending(value, eta);
        emit ChangeQueued(c, value, eta);
    }

    /// @notice Cancel a queued change. The owner can cancel anything; the manager can cancel fee changes.
    function cancelChange(Change c) external {
        if (msg.sender != owner && !(msg.sender == manager && c == Change.FeeBps)) revert NotAuthorized();
        if (pending[c].eta == 0) revert NothingQueued();
        delete pending[c];
        emit ChangeCancelled(c);
    }

    /// @notice Apply a queued change once its 48 hours have passed. Anyone can call it.
    function executeChange(Change c) external {
        Pending memory p = pending[c];
        if (p.eta == 0) revert NothingQueued();
        if (block.timestamp < p.eta) revert TooEarly();
        delete pending[c];
        address a = address(uint160(p.value));
        if (c == Change.Owner) owner = a;
        else if (c == Change.Operator) operator = a;
        else if (c == Change.Manager) manager = a;
        else if (c == Change.FeeRecipient) feeRecipient = a;
        else if (c == Change.FeeBps) feeBps = p.value;
        else maxNavChangeBps = p.value;
        emit ChangeExecuted(c, p.value);
    }

    // =====================================================================================================
    // Manager (personal wallet) or owner: who may deposit, and limits. Immediate: they cannot touch money.
    // =====================================================================================================

    function setAllowed(address member, bool ok) external onlyManagers {
        allowed[member] = ok;
        emit Allowed(member, ok);
    }

    function setLimits(uint256 depositCap_, uint256 minDeposit_) external onlyManagers {
        if (minDeposit_ == 0) revert BadAmount();
        depositCap = depositCap_;
        minDeposit = minDeposit_;
        emit LimitsSet(depositCap_, minDeposit_);
    }

    // =====================================================================================================
    // Views
    // =====================================================================================================

    /// @notice USDT value of one whole share (1e18), in USDT units (6 decimals).
    function sharePrice() public view returns (uint256) {
        uint256 s = totalSupply();
        return s == 0 ? 1e6 : nav * 1e18 / s;
    }

    /// @notice Everything the dashboard needs about one wallet.
    function memberInfo(address m)
        external
        view
        returns (
            uint256 shares,
            uint256 escrowed,
            uint256 value,
            uint256 costBasis,
            uint256 feeIfRedeemed,
            uint256 queuedDeposit,
            uint256 unclaimed,
            bool isAllowed
        )
    {
        shares = balanceOf(m);
        escrowed = pendingRedeem[m];
        uint256 s = totalSupply();
        value = s == 0 ? 0 : (shares + escrowed) * nav / s;
        costBasis = cost[m];
        if (m != feeRecipient && value > costBasis) feeIfRedeemed = (value - costBasis) * feeBps / 10_000;
        queuedDeposit = pendingDeposit[m];
        unclaimed = claimable[m];
        isAllowed = allowed[m];
    }

    function queueLengths() external view returns (uint256 deposits, uint256 redeems) {
        return (depositQueue.length, redeemQueue.length);
    }

    // =====================================================================================================
    // Internals
    // =====================================================================================================

    /// Shares move only by minting, burning, and in and out of this contract's escrow.
    function _update(address from, address to, uint256 value) internal override {
        if (from != address(0) && to != address(0) && from != address(this) && to != address(this)) {
            revert NonTransferable();
        }
        super._update(from, to, value);
    }

    /// The value may move at most maxNavChangeBps from where it stood at the start of the current day-long window,
    /// however many times it is reported.
    function _checkJump(uint256 newNav) private {
        if (newNav == 0) revert ValueJump();
        if (anchorNav == 0 || block.timestamp >= anchorAt + 1 days) (anchorNav, anchorAt) = (nav, block.timestamp);
        uint256 old = anchorNav;
        if (old == 0) return;
        uint256 diff = newNav > old ? newNav - old : old - newNav;
        if (diff * 10_000 > old * maxNavChangeBps) revert ValueJump();
    }

    /// Pay without letting one blocked address stop everyone else's settlement.
    function _tryPay(address to, uint256 amount) private returns (bool) {
        (bool ok, bytes memory ret) = address(asset).call(abi.encodeCall(IERC20.transfer, (to, amount)));
        return ok && (ret.length == 0 || (ret.length >= 32 && abi.decode(ret, (bool))));
    }
}
