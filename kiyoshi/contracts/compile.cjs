const solc=require('solc'),fs=require('fs')
const src=fs.readFileSync(__dirname + '/KiyoshiArb.sol','utf8')
const out=JSON.parse(solc.compile(JSON.stringify({language:'Solidity',sources:{'KiyoshiArb.sol':{content:src}},settings:{optimizer:{enabled:true,runs:200},evmVersion:'shanghai',outputSelection:{'*':{'*':['abi','evm.bytecode.object']}}}})))
for(const e of out.errors||[])console.log(e.severity,e.formattedMessage)
const c=out.contracts?.['KiyoshiArb.sol']?.KiyoshiArb;if(c){fs.writeFileSync(__dirname + '/KiyoshiArb.json',JSON.stringify({abi:c.abi,bytecode:'0x'+c.evm.bytecode.object}));console.log('bytecode bytes',c.evm.bytecode.object.length/2)}
