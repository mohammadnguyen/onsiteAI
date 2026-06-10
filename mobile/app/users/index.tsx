import { useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  FlatList,
  ActivityIndicator,
  Pressable,
  RefreshControl,
  Alert,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter, type Href } from 'expo-router';
import { useTranslation } from 'react-i18next';
import axios from 'axios';

import {
  useUsers,
  useUpdateUser,
  type UserPublic,
  type UserRole,
} from '../../src/api/hooks/useUsers';
import { useMe } from '../../src/api/hooks/useAuth';

/**
 * M4: user/team management list (admin-only).
 *
 * Route: ``/users``. Entered via the admin-only "Users" card on the
 * Settings tab. Backend is authoritative — all three user routes are
 * require_admin; a contributor landing here gets a 403, rendered as
 * the "admins only" state.
 *
 * Row tap opens a native action menu (Alert) with role-change and
 * deactivate/reactivate, each behind its own confirm. The admin's
 * OWN row is informational only: self-deactivation and self-demotion
 * are blocked in the UI (backend permits them when not last-admin —
 * on a phone that is an instant self-logout footgun). Server-side
 * 409 details (admin cap / last-admin protection) are surfaced
 * verbatim.
 */

export default function UsersScreen() {
  const router = useRouter();
  const { t } = useTranslation();
  const me = useMe();
  const users = useUsers();
  const update = useUpdateUser();
  const [userRefreshing, setUserRefreshing] = useState(false);

  const onBack = () => {
    if (router.canGoBack()) router.back();
    else router.replace('/(tabs)/settings');
  };

  const onRefresh = () => {
    setUserRefreshing(true);
    void users.refetch().finally(() => setUserRefreshing(false));
  };

  const isForbidden =
    users.isError &&
    axios.isAxiosError(users.error) &&
    users.error.response?.status === 403;

  const performUpdate = async (
    userId: string,
    patch: { role?: UserRole; is_active?: boolean },
  ) => {
    try {
      await update.mutateAsync({ userId, patch });
    } catch (err) {
      const detail = axios.isAxiosError(err)
        ? err.response?.data?.detail
        : undefined;
      Alert.alert(
        t('common.error'),
        typeof detail === 'string' ? detail : t('users.action_error'),
      );
    }
  };

  const confirmRoleChange = (u: UserPublic, targetRole: UserRole) => {
    const roleLabel = t(
      targetRole === 'admin' ? 'users.role_admin' : 'users.role_contributor',
    );
    Alert.alert(
      t('users.confirm_role_title'),
      t('users.confirm_role_message', { name: u.full_name, role: roleLabel }),
      [
        { text: t('common.cancel'), style: 'cancel' },
        {
          text: t('common.yes'),
          onPress: () => void performUpdate(u.user_id, { role: targetRole }),
        },
      ],
    );
  };

  const confirmActiveToggle = (u: UserPublic) => {
    const deactivating = u.is_active;
    Alert.alert(
      t(
        deactivating
          ? 'users.confirm_deactivate_title'
          : 'users.confirm_reactivate_title',
      ),
      t(
        deactivating
          ? 'users.confirm_deactivate_message'
          : 'users.confirm_reactivate_message',
        { name: u.full_name },
      ),
      [
        { text: t('common.cancel'), style: 'cancel' },
        {
          text: t(deactivating ? 'users.deactivate' : 'users.reactivate'),
          style: deactivating ? 'destructive' : 'default',
          onPress: () =>
            void performUpdate(u.user_id, { is_active: !u.is_active }),
        },
      ],
    );
  };

  const onRowPress = (u: UserPublic) => {
    // Fail closed until we know who "self" is — no actions before
    // /auth/me resolves.
    if (!me.data) return;
    if (u.user_id === me.data.user_id) {
      // UI guard: self-deactivation and self-demotion are blocked
      // entirely (the whole action menu is withheld on the own row).
      Alert.alert(t('users.self_blocked'));
      return;
    }
    const targetRole: UserRole = u.role === 'admin' ? 'contributor' : 'admin';
    Alert.alert(u.full_name, u.email, [
      {
        text: t(
          u.role === 'admin' ? 'users.make_contributor' : 'users.make_admin',
        ),
        onPress: () => confirmRoleChange(u, targetRole),
      },
      {
        text: t(u.is_active ? 'users.deactivate' : 'users.reactivate'),
        style: u.is_active ? 'destructive' : 'default',
        onPress: () => confirmActiveToggle(u),
      },
      { text: t('common.cancel'), style: 'cancel' },
    ]);
  };

  return (
    <SafeAreaView style={s.safe} edges={['top', 'left', 'right']}>
      <View style={s.header}>
        <Pressable
          onPress={onBack}
          hitSlop={12}
          testID="users-back"
          accessibilityRole="button"
          accessibilityLabel={t('expense.back')}
          style={({ pressed }) => [s.backBtn, pressed && s.backBtnPressed]}
        >
          <Text style={s.backChevron}>{'‹'}</Text>
          <Text style={s.backLabel}>{t('expense.back')}</Text>
        </Pressable>
        <Text style={s.headerTitle} numberOfLines={1}>
          {t('users.title')}
        </Text>
        <Pressable
          onPress={() => router.push('/users/new' as unknown as Href)}
          hitSlop={12}
          testID="users-new"
          accessibilityRole="button"
          accessibilityLabel={t('users.new_user')}
          style={({ pressed }) => [s.newBtn, pressed && s.backBtnPressed]}
        >
          <Text style={s.newBtnText}>{'＋'}</Text>
        </Pressable>
      </View>

      <FlatList
        data={users.data ?? []}
        keyExtractor={(u) => u.user_id}
        renderItem={({ item }) => (
          <UserRow
            user={item}
            isSelf={item.user_id === me.data?.user_id}
            disabled={update.isPending}
            onPress={() => onRowPress(item)}
          />
        )}
        style={s.list}
        contentContainerStyle={
          (users.data ?? []).length === 0 ? s.listEmptyContainer : s.listContainer
        }
        refreshControl={
          <RefreshControl
            refreshing={userRefreshing}
            onRefresh={onRefresh}
            tintColor="#1e293b"
          />
        }
        testID="users-list"
        ListEmptyComponent={
          users.isLoading ? (
            <View style={s.state} testID="users-loading">
              <ActivityIndicator color="#1e293b" />
              <Text style={s.stateText}>{t('common.loading')}</Text>
            </View>
          ) : isForbidden ? (
            <View style={s.state} testID="users-forbidden">
              <Text style={s.stateText}>{t('users.forbidden')}</Text>
            </View>
          ) : users.isError ? (
            <View style={s.state} testID="users-error">
              <Text style={[s.stateText, s.errorText]}>
                {t('users.error')}
              </Text>
              <Pressable
                onPress={() => void users.refetch()}
                style={({ pressed }) => [
                  s.linkBtn,
                  pressed && s.linkBtnPressed,
                ]}
                accessibilityRole="button"
                testID="users-retry"
              >
                <Text style={s.linkBtnText}>{t('common.retry')}</Text>
              </Pressable>
            </View>
          ) : (
            <View style={s.state} testID="users-empty">
              <Text style={s.stateText}>{t('users.empty')}</Text>
            </View>
          )
        }
      />
    </SafeAreaView>
  );
}

function UserRow({
  user,
  isSelf,
  disabled,
  onPress,
}: {
  user: UserPublic;
  isSelf: boolean;
  disabled: boolean;
  onPress: () => void;
}) {
  const { t } = useTranslation();
  return (
    <Pressable
      onPress={onPress}
      disabled={disabled}
      testID={`user-row-${user.user_id}`}
      accessibilityRole="button"
      accessibilityLabel={user.full_name}
      hitSlop={4}
      style={({ pressed }) => [s.row, pressed && s.rowPressed]}
    >
      <View style={s.rowTop}>
        <Text style={s.name} numberOfLines={1}>
          {user.full_name}
          {isSelf ? <Text style={s.youText}>{`（${t('users.you')}）`}</Text> : null}
        </Text>
        <View
          style={[s.rolePill, user.role === 'admin' ? s.roleAdmin : s.roleContrib]}
        >
          <Text
            style={[
              s.rolePillText,
              user.role === 'admin' ? s.roleAdminText : s.roleContribText,
            ]}
          >
            {t(user.role === 'admin' ? 'users.role_admin' : 'users.role_contributor')}
          </Text>
        </View>
      </View>
      <View style={s.rowBottom}>
        <Text style={s.email} numberOfLines={1}>
          {user.email}
        </Text>
        {!user.is_active ? (
          <View style={s.inactivePill} testID={`user-inactive-${user.user_id}`}>
            <Text style={s.inactiveText}>{t('users.inactive')}</Text>
          </View>
        ) : null}
      </View>
    </Pressable>
  );
}

const s = StyleSheet.create({
  safe: { flex: 1, backgroundColor: '#ffffff' },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 8,
    paddingVertical: 8,
    borderBottomWidth: 1,
    borderBottomColor: '#e2e8f0',
  },
  backBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 8,
    paddingVertical: 4,
    minWidth: 72,
  },
  backBtnPressed: { opacity: 0.5 },
  backChevron: {
    fontSize: 28,
    color: '#1e293b',
    marginRight: 4,
    lineHeight: 28,
  },
  backLabel: { fontSize: 16, color: '#1e293b' },
  headerTitle: {
    flex: 1,
    textAlign: 'center',
    fontSize: 17,
    fontWeight: '600',
    color: '#0f172a',
  },
  newBtn: {
    minWidth: 72,
    alignItems: 'flex-end',
    paddingHorizontal: 12,
    paddingVertical: 4,
  },
  newBtnText: { fontSize: 22, color: '#1e293b', fontWeight: '600' },
  list: { flex: 1 },
  listContainer: { paddingHorizontal: 16 },
  listEmptyContainer: { flexGrow: 1, justifyContent: 'center' },
  state: { alignItems: 'center', padding: 24, gap: 12 },
  stateText: { color: '#64748b', fontSize: 15 },
  errorText: { color: '#b91c1c' },
  linkBtn: { paddingHorizontal: 12, paddingVertical: 8 },
  linkBtnPressed: { opacity: 0.5 },
  linkBtnText: { color: '#1e293b', fontSize: 15, fontWeight: '600' },
  row: {
    paddingVertical: 12,
    borderBottomWidth: 1,
    borderBottomColor: '#e2e8f0',
  },
  rowPressed: { backgroundColor: '#f1f5f9' },
  rowTop: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  name: { fontSize: 16, fontWeight: '600', color: '#0f172a', flexShrink: 1 },
  youText: { color: '#64748b', fontWeight: '400', fontSize: 14 },
  rolePill: {
    paddingHorizontal: 10,
    paddingVertical: 3,
    borderRadius: 12,
    marginLeft: 8,
  },
  roleAdmin: { backgroundColor: '#1e293b' },
  roleContrib: { backgroundColor: '#e2e8f0' },
  rolePillText: { fontSize: 11, fontWeight: '600' },
  roleAdminText: { color: '#ffffff' },
  roleContribText: { color: '#334155' },
  rowBottom: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginTop: 4,
  },
  email: { color: '#64748b', fontSize: 13, flexShrink: 1 },
  inactivePill: {
    paddingHorizontal: 8,
    paddingVertical: 2,
    borderRadius: 10,
    backgroundColor: '#fee2e2',
    marginLeft: 8,
  },
  inactiveText: { fontSize: 10, fontWeight: '600', color: '#991b1b' },
});
