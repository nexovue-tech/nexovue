export interface Cart {
  id: string;
  items?: string[];
}

export function itemCount(cart: Cart): number {
  return (cart.items as string[]).length;
}
