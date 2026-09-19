/* Disposable PostgreSQL (PGlite) schema/transaction regressions, no production DB.
 * Optional dev dependency: install @electric-sql/pglite in a scratch directory.
 * NODE_PATH=/scratch/node_modules node tests/test_fills_audit_schema.cjs
 */
const { PGlite } = require('@electric-sql/pglite');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const ddl = fs.readFileSync(path.join(__dirname, '../migrations/076_fills_audit.sql'), 'utf8');

async function check(upgrade) {
  const db = new PGlite();
  await db.exec(`CREATE TABLE paper_trades (id bigint PRIMARY KEY, exit_px numeric, legs jsonb);
    INSERT INTO paper_trades VALUES (1,NULL,NULL);
    CREATE TABLE fill_audit (id integer PRIMARY KEY, evidence text);
    INSERT INTO fill_audit VALUES (1,'forensic evidence');`);
  if (upgrade) {
    // The previously reviewed draft, with its original constraint names.
    await db.exec(`CREATE TABLE fills_audit (
      id bigserial PRIMARY KEY, paper_trade_id bigint NOT NULL REFERENCES paper_trades(id),
      book text NOT NULL, ticker text NOT NULL, event text NOT NULL CHECK(event IN ('entry','exit')),
      fill_kind text, expected_px numeric, got_px numeric NOT NULL,
      gap_through boolean NOT NULL DEFAULT false, phantom_stop boolean NOT NULL DEFAULT false,
      bar jsonb, evidence jsonb, provenance text NOT NULL DEFAULT 'live'
      CHECK(provenance IN ('live','backfill_inferred')), created_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE(paper_trade_id,event));
      INSERT INTO fills_audit(paper_trade_id,book,ticker,event,got_px)
      VALUES(1,'two_test','TEST','entry',100);`);
  }
  // Same lock/transaction as ensure_schema; run twice for repeat deployment.
  for (let i=0; i<2; i++) {
    await db.transaction(async tx => {
      await tx.query('SELECT pg_advisory_xact_lock(760076)');
      await tx.exec(ddl);
    });
  }
  const insert = `INSERT INTO fills_audit(paper_trade_id,book,ticker,event,got_px,leg_index)
    VALUES(1,'two_test','TEST',$1,$2,$3)`;
  if (!upgrade) await db.query(insert, ['entry',100,0]);
  await db.transaction(async tx => {
    await tx.query("UPDATE paper_trades SET legs='[{\"frac\":0.5,\"px\":101}]'::jsonb WHERE id=1");
    await tx.query(insert, ['leg',101,1]);
  });
  const before = (await db.query('SELECT * FROM paper_trades')).rows;
  // Failing a real audit constraint after a real trade UPDATE rolls back both.
  await assert.rejects(db.transaction(async tx => {
    await tx.query('UPDATE paper_trades SET exit_px=102 WHERE id=1');
    await tx.query(insert, ['leg',102,2]);
    await tx.query(insert, ['exit',null,0]); // required got_px
  }), /null value/);
  assert.deepEqual((await db.query('SELECT * FROM paper_trades')).rows, before);
  assert.equal((await db.query("SELECT count(*)::int AS n FROM fills_audit WHERE leg_index=2")).rows[0].n, 0);
  await db.transaction(async tx => {
    await tx.query('UPDATE paper_trades SET exit_px=101.5 WHERE id=1');
    await tx.query(insert, ['leg',102,2]);
    await tx.query(insert, ['exit',101.5,0]);
  });
  await assert.rejects(db.query(insert, ['leg',102,2]), /duplicate key/);
  await assert.rejects(db.query(insert, ['entry',100,1]), /check constraint/);
  await assert.rejects(db.query(insert, ['leg',100,0]), /check constraint/);
  const replay = await db.query(insert + ' ON CONFLICT(paper_trade_id,event,leg_index) DO NOTHING', ['leg',102,2]);
  assert.equal(replay.affectedRows, 0);
  await db.exec(ddl); // preserve existing rows across a later boot too
  assert.equal((await db.query('SELECT count(*)::int AS n FROM fills_audit')).rows[0].n, 4);
  assert.deepEqual((await db.query('SELECT * FROM fill_audit')).rows,
                   [{id:1,evidence:'forensic evidence'}]);
  await db.close();
  console.log(`ok ${upgrade ? 'draft upgrade' : 'fresh install'}: repeat migration, legs, rollback, retry, forensic isolation`);
}
(async () => { await check(false); await check(true); })().catch(e => { console.error(e); process.exitCode=1; });
