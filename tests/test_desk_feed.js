// Run the actual Desk Floor script through success, lost auth and recovery.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const html = fs.readFileSync(path.join(__dirname, '../dashboard/static/desk.html'), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
const elements = new Map();
function element() {
  const classes = new Set();
  return {textContent: '', innerHTML: '', style: {}, appendChild() {},
    classList: {add: (...xs) => xs.forEach(x=>classes.add(x)),
      remove: (...xs) => xs.forEach(x=>classes.delete(x)),
      toggle: (x,on) => on ? classes.add(x) : classes.delete(x)}};
}
function el(id) {
  if (!elements.has(id)) elements.set(id, element());
  return elements.get(id);
}
const data = {jobs: [], gamma: [], macro: [], drift: {sent:0,suppressed:0},
  books:[{book:'TEST',realized_r:2,wins:1,resolved:1,open:3}],specs_today:[],squawk:[]};
let response = 200, requests=0, timerId=0;
const timers = new Map();
const context = vm.createContext({Date, Intl, Math, console,
  document: {getElementById:el, createElement:element, body:{}},
  setInterval: (fn,ms) => {timers.set(++timerId,{fn,ms}); return timerId;},
  clearInterval: id => timers.delete(id),
  fetch: async () => {requests++; return {status:response,ok:response===200,json:async()=>data};}
});
const feedTimers = () => [...timers.values()].filter(t=>t.ms===60000);
async function run() {
  vm.runInContext(script, context);
  await new Promise(resolve=>setImmediate(resolve));
  assert.match(el('books').innerHTML,/3 open/);
  assert.equal(feedTimers().length,1);
  response=401;
  await vm.runInContext('refresh()',context);
  assert.match(el('err').innerHTML,/Updates are paused/);
  assert.equal(el('books').textContent,'Data unavailable');
  assert.equal(el('tape').textContent,'Data unavailable');
  assert.equal(feedTimers().length,0);
  const stoppedAt=requests;
  for (const t of timers.values()) t.fn();
  assert.equal(requests,stoppedAt,'clock tick must not poll unauthorized API');
  response=200;
  await el('resume-feed').onclick();
  assert.equal(feedTimers().length,1);
  assert.equal(el('err').textContent,'');
  assert.match(el('books').innerHTML,/3 open/);
  await vm.runInContext('startFeed()',context);
  assert.equal(feedTimers().length,1,'recovery must not multiply polling');
  response=500;
  await vm.runInContext('refresh()',context);
  assert.match(el('err').textContent,/HTTP 500/);
  assert.equal(el('books').textContent,'Data unavailable');
  assert.equal(feedTimers().length,1,'transient failures still retry');
  console.log('ok: actual desk script clears stale data, pauses on 401 and resumes after sign-in');
}
run().catch(e=>{console.error(e);process.exitCode=1;});
