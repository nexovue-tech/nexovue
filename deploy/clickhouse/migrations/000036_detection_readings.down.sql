-- Losing the readings loses the record of past judgements, not the findings:
-- each finding keeps its own copy of what it was judged on (AC40).
DROP TABLE IF EXISTS detection_readings
