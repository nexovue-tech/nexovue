-- Every row is re-derivable from the `browser.web_vital` log records in `logs`,
-- which the browser door writes independently of this table. One line.
DROP TABLE IF EXISTS web_vitals
