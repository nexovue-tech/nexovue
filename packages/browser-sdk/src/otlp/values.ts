/**
 * OTLP/JSON attribute construction.
 *
 * Two rules hold everywhere: an unavailable value is left UNSET rather than sent
 * as "" or "unknown", because "" is a value someone will filter on; and a dotted
 * key is a path in the storage pipeline, so a flat attribute sharing a prefix
 * with a dotted one silently loses to it.
 */

export type AnyValue =
  | { stringValue: string }
  | { boolValue: boolean }
  | { doubleValue: number }
  | { intValue: string }
  | { arrayValue: { values: AnyValue[] } };

export interface KeyValue {
  key: string;
  value: AnyValue;
}

export function stringValue(value: string): AnyValue {
  return { stringValue: value };
}

/** Collects attributes, skipping every value the browser could not supply. */
export class AttributeList {
  private readonly entries: KeyValue[] = [];

  string(key: string, value: string | null | undefined): this {
    if (value !== null && value !== undefined && value !== "") {
      this.entries.push({ key, value: { stringValue: value } });
    }
    return this;
  }

  double(key: string, value: number | null | undefined): this {
    if (typeof value === "number" && Number.isFinite(value)) {
      this.entries.push({ key, value: { doubleValue: value } });
    }
    return this;
  }

  int(key: string, value: number | null | undefined): this {
    if (typeof value === "number" && Number.isFinite(value)) {
      this.entries.push({ key, value: { intValue: String(Math.trunc(value)) } });
    }
    return this;
  }

  bool(key: string, value: boolean | null | undefined): this {
    if (typeof value === "boolean") {
      this.entries.push({ key, value: { boolValue: value } });
    }
    return this;
  }

  strings(key: string, values: readonly string[] | null | undefined): this {
    if (values && values.length > 0) {
      this.entries.push({
        key,
        value: { arrayValue: { values: values.map(stringValue) } },
      });
    }
    return this;
  }

  build(): KeyValue[] {
    return this.entries;
  }
}
