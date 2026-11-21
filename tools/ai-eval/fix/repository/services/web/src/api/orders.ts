import { toCents } from "../format/money";

export interface Order {
  total: string;
  totalCents: number;
}

export function submitOrder(order: Order): number {
  const cents = toCents(order.total);
  if (cents !== order.totalCents) {
    throw new Error("total mismatch");
  }
  return cents;
}
