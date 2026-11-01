const assert=require('node:assert/strict');
const {frameTiming}=require('../dashboard/static/trading.js');
assert.equal(frameTiming({preview:'recorded'},1000,10,20),null);
const sample=frameTiming({sent_at:1,market_at:.8,selected:{market_at:.7},version:4},900,10,25);
assert.equal(sample.frame_age_ms,-100); // Preserve clock skew, never hide it with max(0).
assert.equal(sample.latest_event_age_ms,100);
assert.equal(sample.selected_event_age_ms,200);
assert.equal(sample.callback_ms,15);
assert.equal(frameTiming({},1000,10,20).frame_age_ms,null);
assert.equal(frameTiming({},1000,10,20).latest_event_age_ms,null);
console.log('Browser timing tests passed');
