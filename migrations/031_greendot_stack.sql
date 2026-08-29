-- 031: The full-stack dot study — Eric's actual entry, graded. Every
-- recorded 16D dot tagged with its state legs at the dot bar (%R
-- floored-and-turning, RSI depressed-and-turning, MACD below zero
-- curving up, money flow raw) plus the location proxy (time-at-price
-- shelf days). Cuts happen at readout; the tags never filter.

CREATE TABLE IF NOT EXISTS greendot_stack (
    id          serial PRIMARY KEY,
    dot_id      integer NOT NULL REFERENCES greendot_dots(id)
                ON DELETE CASCADE,
    pctr        numeric,
    pctr_turn   boolean,
    rsi         numeric,
    rsi_turn    boolean,
    macd_line   numeric,
    macd_turn   boolean,
    mf          numeric,
    shelf_days  integer,
    note        text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (dot_id)
);
