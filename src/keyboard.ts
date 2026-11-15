export type CompositionKeyEvent = {
  nativeEvent: { isComposing: boolean };
  keyCode: number;
};

/** Enter/Escape handlers must ignore keystrokes that belong to an IME candidate. */
export function isCompositionKey(event: CompositionKeyEvent): boolean {
  return event.nativeEvent.isComposing || event.keyCode === 229;
}
