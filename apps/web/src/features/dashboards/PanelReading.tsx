import type { StoredPanel } from "./api";
import type { OkResult } from "./kept-readings";
import { NumberPanel } from "./NumberPanel";
import { PanelWords } from "./PanelFrame";
import { TABLE_KINDS, TIME_KINDS } from "./panel-words";
import { TablePanel } from "./TablePanel";
import { TimePanel } from "./TimePanel";
import type { Marks } from "./time-lines";

/**
 * One panel's reading, drawn by its kind: a chart, a number or a table (F20, T5.6a). The
 * read view, the builder and the side sheet's preview all draw through this, so a draft
 * looks as the saved dashboard will (FR49).
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
}: {
  panel: StoredPanel;
  reading: OkResult;
  zone: string;
  description: string;
  syncKey?: string;
  onDrag?: (fromSeconds: number, toSeconds: number) => void;
  marks?: Marks;
  asTable?: boolean;
}) {
  if (TIME_KINDS.has(panel.kind)) {
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
      />
    );
  }
  if (reading.number) return <NumberPanel panel={panel} number={reading.number} />;
  if (TABLE_KINDS.has(panel.kind) && reading.rows) {
    return <TablePanel kind={panel.kind} rows={reading.rows} caption={description} zone={zone} />;
  }
  return <PanelWords words="This panel type is no longer available." />;
}
