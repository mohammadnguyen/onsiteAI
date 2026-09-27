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
    /** A string for a host node ('View', 'Text'); the component otherwise. */
    type: unknown;
    props: Record<string, any>;
    findByProps(props: Record<string, unknown>): ReactTestInstance;
    findAllByProps(props: Record<string, unknown>): ReactTestInstance[];
    findAllByType(type: unknown): ReactTestInstance[];
    findAll(predicate: (node: ReactTestInstance) => boolean): ReactTestInstance[];
  }

  export interface ReactTestRenderer {
    root: ReactTestInstance;
    update(element: ReactElement): void;
    unmount(): void;
    toJSON(): unknown;
  }

  export function create(element: ReactElement): ReactTestRenderer;
  export function act(callback: () => void | Promise<void>): Promise<void> & { then?: never };
}
