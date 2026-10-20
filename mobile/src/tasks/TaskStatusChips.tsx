import { StyleSheet, View } from 'react-native';
import { useTranslation } from 'react-i18next';

import { TASK_STATUSES, type TaskStatus } from '../api/tasks';
import { Chip } from '../ui/kit';

/**
 * The four statuses as one-tap chips (plan v2 §6). Any status can move to
 * any other. `disabled` comes from the server's `permissions.change_status`;
 * the chips still show where the task stands.
 */
export function TaskStatusChips({
  value,
  disabled,
  onChange,
}: {
  value: TaskStatus;
  disabled: boolean;
  onChange: (next: TaskStatus) => void;
}) {
  const { t } = useTranslation();
  return (
    <View style={s.row} accessibilityRole="radiogroup">
      {TASK_STATUSES.map((status) => (
        <Chip
          key={status}
          label={t(`tasks.status.${status}`)}
          selected={status === value}
          disabled={disabled}
          accessibilityRole="radio"
          onPress={() => {
            if (!disabled && status !== value) onChange(status);
          }}
          testID={`task-status:${status}`}
        />
      ))}
    </View>
  );
}

const s = StyleSheet.create({
  row: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
});
