import type { StoredPanel } from "./api";
import type { OkResult } from "./kept-readings";
import { NumberPanel } from "./NumberPanel";
import { PanelWords } from "./PanelFrame";
import { combinedWords, mixedUnitsWords, TABLE_KINDS, TIME_KINDS } from "./panel-words";
import { SloBudgetPanel } from "./SloBudgetPanel";
import { TablePanel } from "./TablePanel";
import { TimePanel } from "./TimePanel";
import type { Marks } from "./time-lines";

/**
 * One panel's reading, drawn by its kind: a chart, a number, a table, or a reliability
 * target's standing (F20, T5.6a, T10.2d). The read view, the builder and the side sheet's
 * preview all draw through this, so a draft looks as the saved dashboard will (FR49).
 */
export function PanelReading({
  panel,
  reading,
  zone,
  description,
  syncKey,
  onDrag,
  marks,
  asTable = false,
  now = Date.now(),
}: {
  panel: StoredPanel;
  reading: OkResult;
  zone: string;
  description: string;
  syncKey?: string;
  onDrag?: (fromSeconds: number, toSeconds: number) => void;
  marks?: Marks;
  asTable?: boolean;
  /** The reader's clock, for a reliability target's "as of" and staleness. */
  now?: number;
}) {
  // Two units cannot be added into one line, so none is drawn (AC51).
  const mixed = mixedUnitsWords(panel, reading.units);
  if (mixed !== undefined) return <PanelWords words={mixed} />;
  if (TIME_KINDS.has(panel.kind)) {
    const combined = combinedWords(panel, reading.series_combined);
    return (
      <TimePanel
        panel={panel}
        reading={reading}
        zone={zone}
        description={description}
        asTable={asTable}
        {...(syncKey === undefined ? {} : { syncKey })}
        {...(onDrag === undefined ? {} : { onDrag })}
        {...(marks === undefined ? {} : { marks })}
        {...(combined === undefined ? {} : { note: combined })}
      />
    );
  }
  if (reading.number) return <NumberPanel panel={panel} number={reading.number} />;
  if (reading.budget) return <SloBudgetPanel budget={reading.budget} now={now} />;
  if (TABLE_KINDS.has(panel.kind) && reading.rows) {
    return <TablePanel kind={panel.kind} rows={reading.rows} caption={description} zone={zone} />;
  }
  return <PanelWords words="This panel type is no longer available." />;
}
