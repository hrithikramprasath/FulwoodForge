import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

/*
 * jsdom lacks several browser APIs that Radix primitives (Select, Dialog, Tabs,
 * Switch) rely on. Polyfilled here so portals and popovers render with zero
 * runtime warnings.
 */
class ResizeObserverStub {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}
globalThis.ResizeObserver = ResizeObserverStub;

if (!window.matchMedia) {
  window.matchMedia = (query: string): MediaQueryList =>
    ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    }) as MediaQueryList;
}

if (
  typeof (window as { PointerEvent?: unknown }).PointerEvent === "undefined"
) {
  class PointerEventPolyfill extends MouseEvent {
    readonly pointerId: number;
    readonly pointerType: string;
    constructor(type: string, init: PointerEventInit = {}) {
      super(type, init);
      this.pointerId = init.pointerId ?? 1;
      this.pointerType = init.pointerType ?? "mouse";
    }
  }
  (window as { PointerEvent?: unknown }).PointerEvent = PointerEventPolyfill;
}

Element.prototype.scrollIntoView = function scrollIntoView(): void {};
Element.prototype.hasPointerCapture = function hasPointerCapture(): boolean {
  return false;
};
Element.prototype.setPointerCapture = function setPointerCapture(): void {};
Element.prototype.releasePointerCapture =
  function releasePointerCapture(): void {};

afterEach(() => cleanup());
