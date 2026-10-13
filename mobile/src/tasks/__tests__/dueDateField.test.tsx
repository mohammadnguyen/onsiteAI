/**
 * The due date typed as text, where there is no native calendar (web):
 * text becomes the value when the field is left, and until then the field
 * says whether it could - so a form never saves the old value in place of a
 * mistyped one.
 */
import { act, create, type ReactTestInstance, type ReactTestRenderer } from 'react-test-renderer';
import React from 'react';

// No native calendar: the field falls back to typed text.
jest.mock('@react-native-community/datetimepicker', () => ({ __esModule: true, default: null }));
jest.mock('react-i18next', () => ({ useTranslation: () => ({ t: (k: string) => k }) }));

import { DueDateField } from '../DueDateField';

function typed(tree: ReactTestRenderer): ReactTestInstance {
  const [input] = tree.root.findAll(
    (n) => n.props.testID === 'due-date-typed' && typeof n.type !== 'string',
  );
  if (!input) throw new Error('no typed due date');
  return input;
}

function render(onChange: jest.Mock, onValidityChange: jest.Mock): ReactTestRenderer {
  let tree: ReactTestRenderer | undefined;
  act(() => {
    tree = create(
      <DueDateField
        value={null}
        disabled={false}
        onChange={onChange}
        onValidityChange={onValidityChange}
      />,
    );
  });
  return tree as ReactTestRenderer;
}

it('a date that does not exist is reported at once, and is never applied', () => {
  const onChange = jest.fn();
  const onValidityChange = jest.fn();
  const tree = render(onChange, onValidityChange);
  act(() => typed(tree).props.onChangeText('31/02/2026'));
  expect(onValidityChange).toHaveBeenLastCalledWith(false);
  act(() => typed(tree).props.onBlur());
  expect(onChange).not.toHaveBeenCalled();
  const said = tree.root.findAll(
    (n) => typeof n.type === 'string' && n.props.children === 'tasks.due.invalid',
  );
  expect(said.length).toBeGreaterThan(0);
});

it('a real date is valid as typed and becomes the value when the field is left', () => {
  const onChange = jest.fn();
  const onValidityChange = jest.fn();
  const tree = render(onChange, onValidityChange);
  act(() => typed(tree).props.onChangeText('17/10/2026'));
  expect(onValidityChange).toHaveBeenLastCalledWith(true);
  act(() => typed(tree).props.onBlur());
  expect(onChange).toHaveBeenCalledWith('2026-10-17');
});

it('emptying the text makes it valid again', () => {
  const onValidityChange = jest.fn();
  const tree = render(jest.fn(), onValidityChange);
  act(() => typed(tree).props.onChangeText('31/02'));
  act(() => typed(tree).props.onChangeText(''));
  expect(onValidityChange).toHaveBeenLastCalledWith(true);
});
