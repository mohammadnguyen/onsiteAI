/**
 * The slice of react-test-renderer these tests use.
 *
 * The renderer itself ships with jest-expo; only its types are missing.
 * Declared here rather than adding another dependency for type
 * definitions alone - and kept to what is actually called, so an
 * unsupported API does not silently type-check.
 */
declare module 'react-test-renderer' {
  import type { ReactElement } from 'react';

  export interface ReactTestInstance {
    props: Record<string, any>;
    findByProps(props: Record<string, unknown>): ReactTestInstance;
    findAllByType(type: unknown): ReactTestInstance[];
  }

  export interface ReactTestRenderer {
    root: ReactTestInstance;
    unmount(): void;
    toJSON(): unknown;
  }

  export function create(element: ReactElement): ReactTestRenderer;
  export function act(callback: () => void | Promise<void>): Promise<void> & { then?: never };
}
