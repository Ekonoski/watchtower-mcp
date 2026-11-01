const assert = require('node:assert/strict');
const {paperGeometry:g}=require('../dashboard/static/trading.js');
let r=g(6,5.8,6.4,20);
assert.equal(r.shares,100);assert.ok(Math.abs(r.rewardRisk-2)<1e-10);
assert.ok(r.shares*r.risk<=20+1e-10);
for(const args of [[6,6,7,20],[6,7,8,20],[6,5,5.5,20],[6,5,7,0],['',5,7,20],[6,5,7,Infinity],[6,5,7,.1]]) assert.ok(g(...args).error);
r=g(.95,.91,1.03,10);assert.equal(r.shares,250);
console.log('Paper geometry: 9 cases passed.');
