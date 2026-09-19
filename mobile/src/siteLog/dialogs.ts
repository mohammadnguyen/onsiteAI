import { Alert, Platform } from 'react-native';

/**
 * Asking the user something, on every platform this app runs on.
 *
 * `Alert.alert` is a no-op on web, so a Discard button wired straight to it
 * silently does nothing - and the draft it cannot remove still counts
 * against the per-account capacity.
 */
export function confirmDestructive(args: {
  title: string;
  body: string;
  confirmLabel: string;
  cancelLabel: string;
  onConfirm: () => void;
}): void {
  if (Platform.OS === 'web') {
    // eslint-disable-next-line no-alert
    if (globalThis.confirm?.(`${args.title}\n\n${args.body}`)) args.onConfirm();
    return;
  }
  Alert.alert(args.title, args.body, [
    { text: args.cancelLabel, style: 'cancel' },
    { text: args.confirmLabel, style: 'destructive', onPress: args.onConfirm },
  ]);
}

/** Telling the user something, with one way on and one way off it. */
export function notify(args: {
  title: string;
  body: string;
  okLabel: string;
  onOk: () => void;
}): void {
  if (Platform.OS === 'web') {
    // eslint-disable-next-line no-alert
    globalThis.alert?.(`${args.title}\n\n${args.body}`);
    args.onOk();
    return;
  }
  Alert.alert(args.title, args.body, [{ text: args.okLabel, onPress: args.onOk }]);
}
