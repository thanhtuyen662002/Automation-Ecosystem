import React from 'react';
import { Bot, KeyRound, Plus, Play, RefreshCw, Save, Trash2 } from 'lucide-react';

import { Badge, PageHeader, Skeleton } from '@/components/ui';
import type { AiKey, AiKeyPayload, AiModel, AiProvider } from '@/lib/api';
import {
  useAiProviders,
  useCreateAiKey,
  useCreateAiModel,
  useCreateAiProvider,
  useDeleteAiKey,
  useDeleteAiModel,
  useDeleteAiProvider,
  useTestAiProvider,
  useUpdateAiKey,
  useUpdateAiModel,
  useUpdateAiProvider,
} from '@/lib/hooks';

type ProviderDraft = {
  provider: string;
  display_name: string;
  base_url: string;
  enabled: boolean;
  priority: string;
};

type KeyDraft = {
  label: string;
  raw_key: string;
  enabled: boolean;
  priority: string;
};

type ModelDraft = {
  model_name: string;
  display_name: string;
  enabled: boolean;
  is_default: boolean;
  max_tokens: string;
  temperature_default: string;
  priority: string;
};

const PROVIDERS_REQUIRING_KEYS = new Set(['openai', 'gemini', 'huggingface']);

const inputStyle: React.CSSProperties = { minWidth: 0 };
const cellInputStyle: React.CSSProperties = { ...inputStyle, padding: '0.36rem 0.55rem', fontSize: '0.75rem' };

function boolToggle(checked: boolean, onChange: (checked: boolean) => void, title: string) {
  return (
    <label className="toggle" title={title}>
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <span className="toggle-track" />
      <span className="toggle-thumb" />
    </label>
  );
}

function numberValue(value: string, fallback = 100): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function optionalInt(value: string): number | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  const parsed = Number(trimmed);
  return Number.isFinite(parsed) ? Math.max(1, Math.trunc(parsed)) : null;
}

function optionalFloat(value: string): number | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  const parsed = Number(trimmed);
  return Number.isFinite(parsed) ? parsed : null;
}

function formatTime(ts: number | null): string {
  if (!ts) return 'Never';
  return new Date(ts * 1000).toLocaleString();
}

function keyDraftFrom(key: AiKey): KeyDraft {
  return {
    label: key.label,
    raw_key: '',
    enabled: key.enabled,
    priority: String(key.priority),
  };
}

function modelDraftFrom(model: AiModel): ModelDraft {
  return {
    model_name: model.model_name,
    display_name: model.display_name,
    enabled: model.enabled,
    is_default: model.is_default,
    max_tokens: model.max_tokens == null ? '' : String(model.max_tokens),
    temperature_default: model.temperature_default == null ? '' : String(model.temperature_default),
    priority: String(model.priority),
  };
}

function providerDraftFrom(provider: AiProvider): ProviderDraft {
  return {
    provider: provider.provider,
    display_name: provider.display_name,
    base_url: provider.base_url ?? '',
    enabled: provider.enabled,
    priority: String(provider.priority),
  };
}

function FieldLabel({ children }: { children: React.ReactNode }) {
  return <div style={{ fontSize: '0.68rem', color: 'var(--text-muted)', marginBottom: '0.25rem', fontWeight: 600 }}>{children}</div>;
}

function WarningText({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ color: 'var(--warning)', fontSize: '0.75rem', display: 'flex', gap: '0.35rem', alignItems: 'center' }}>
      <span style={{ width: 7, height: 7, borderRadius: 7, background: 'var(--warning)', display: 'inline-block' }} />
      {children}
    </div>
  );
}

export function SettingsAI() {
  const providersQuery = useAiProviders();
  const providers = providersQuery.data?.items ?? [];

  const createProvider = useCreateAiProvider();
  const updateProvider = useUpdateAiProvider();
  const deleteProvider = useDeleteAiProvider();
  const createKey = useCreateAiKey();
  const updateKey = useUpdateAiKey();
  const deleteKey = useDeleteAiKey();
  const createModel = useCreateAiModel();
  const updateModel = useUpdateAiModel();
  const deleteModel = useDeleteAiModel();
  const testAi = useTestAiProvider();

  const [showProviderForm, setShowProviderForm] = React.useState(false);
  const [newProvider, setNewProvider] = React.useState<ProviderDraft>({
    provider: '',
    display_name: '',
    base_url: '',
    enabled: true,
    priority: '100',
  });
  const [providerEdits, setProviderEdits] = React.useState<Record<string, ProviderDraft>>({});
  const [newKeys, setNewKeys] = React.useState<Record<string, KeyDraft>>({});
  const [keyEdits, setKeyEdits] = React.useState<Record<string, KeyDraft>>({});
  const [newModels, setNewModels] = React.useState<Record<string, ModelDraft>>({});
  const [modelEdits, setModelEdits] = React.useState<Record<string, ModelDraft>>({});
  const [message, setMessage] = React.useState('');
  const [error, setError] = React.useState('');
  const [testState, setTestState] = React.useState({
    provider_id: '',
    model_id: '',
    key_id: '',
    prompt: 'Trả lời bằng một câu ngắn gọn để xác nhận nhà cung cấp này đang hoạt động.',
    max_tokens: '128',
    temperature: '0.2',
  });

  const allModels = providers.flatMap((provider) => provider.models.map((model) => ({ provider, model })));
  const allKeys = providers.flatMap((provider) => provider.keys.map((key) => ({ provider, key })));
  const selectedProvider = providers.find((provider) => provider.id === testState.provider_id);
  const visibleModels = selectedProvider
    ? selectedProvider.models.map((model) => ({ provider: selectedProvider, model }))
    : allModels;
  const visibleKeys = selectedProvider
    ? selectedProvider.keys.map((key) => ({ provider: selectedProvider, key }))
    : allKeys;

  function setProviderEdit(id: string, patch: Partial<ProviderDraft>) {
    const current = providers.find((provider) => provider.id === id);
    if (!current) return;
    setProviderEdits((prev) => ({ ...prev, [id]: { ...(prev[id] ?? providerDraftFrom(current)), ...patch } }));
  }

  function setNewKeyDraft(providerId: string, patch: Partial<KeyDraft>) {
    setNewKeys((prev) => {
      const base: KeyDraft = prev[providerId] ?? {
        label: 'Main key',
        raw_key: '',
        enabled: true,
        priority: '100',
      };
      return { ...prev, [providerId]: { ...base, ...patch } };
    });
  }

  function setNewModelDraft(providerId: string, patch: Partial<ModelDraft>) {
    setNewModels((prev) => {
      const base: ModelDraft = prev[providerId] ?? {
        model_name: '',
        display_name: '',
        enabled: true,
        is_default: false,
        max_tokens: '',
        temperature_default: '',
        priority: '100',
      };
      return { ...prev, [providerId]: { ...base, ...patch } };
    });
  }

  function setKeyEdit(key: AiKey, patch: Partial<KeyDraft>) {
    setKeyEdits((prev) => ({ ...prev, [key.id]: { ...(prev[key.id] ?? keyDraftFrom(key)), ...patch } }));
  }

  function setModelEdit(model: AiModel, patch: Partial<ModelDraft>) {
    setModelEdits((prev) => ({ ...prev, [model.id]: { ...(prev[model.id] ?? modelDraftFrom(model)), ...patch } }));
  }

  async function saveProvider(provider: AiProvider) {
    const draft = providerEdits[provider.id] ?? providerDraftFrom(provider);
    setError('');
    await updateProvider.mutateAsync({
      id: provider.id,
      payload: {
        provider: draft.provider.trim(),
        display_name: draft.display_name.trim(),
        base_url: draft.base_url.trim() || null,
        enabled: draft.enabled,
        priority: numberValue(draft.priority, provider.priority),
      },
    });
    setProviderEdits((prev) => {
      const next = { ...prev };
      delete next[provider.id];
      return next;
    });
    setMessage('Đã lưu nhà cung cấp.');
  }

  async function addProvider() {
    setError('');
    if (!newProvider.provider.trim() || !newProvider.display_name.trim()) {
      setError('Tên nhà cung cấp và tên hiển thị là bắt buộc.');
      return;
    }
    await createProvider.mutateAsync({
      provider: newProvider.provider.trim(),
      display_name: newProvider.display_name.trim(),
      base_url: newProvider.base_url.trim() || null,
      enabled: newProvider.enabled,
      priority: numberValue(newProvider.priority),
    });
    setNewProvider({ provider: '', display_name: '', base_url: '', enabled: true, priority: '100' });
    setShowProviderForm(false);
    setMessage('Đã tạo nhà cung cấp.');
  }

  async function addKey(providerId: string) {
    const draft = newKeys[providerId];
    setError('');
    if (!draft?.label.trim() || !draft.raw_key.trim()) {
      setError('Nhãn khóa và API key là bắt buộc.');
      return;
    }
    await createKey.mutateAsync({
      providerId,
      payload: {
        label: draft.label.trim(),
        raw_key: draft.raw_key.trim(),
        enabled: draft.enabled,
        priority: numberValue(draft.priority),
      },
    });
    setNewKeys((prev) => ({ ...prev, [providerId]: { ...draft, raw_key: '' } }));
    setMessage('Đã lưu API key. Khóa gốc đã được mã hóa và xóa khỏi biểu mẫu.');
  }

  async function saveKey(key: AiKey) {
    const draft = keyEdits[key.id] ?? keyDraftFrom(key);
    setError('');
    const payload: AiKeyPayload = {
      label: draft.label.trim(),
      enabled: draft.enabled,
      priority: numberValue(draft.priority, key.priority),
    };
    if (draft.raw_key.trim()) {
      payload.raw_key = draft.raw_key.trim();
    }
    await updateKey.mutateAsync({ keyId: key.id, payload });
    setKeyEdits((prev) => {
      const next = { ...prev };
      delete next[key.id];
      return next;
    });
    setMessage('Đã cập nhật API key. Khóa mới đã được mã hóa và xóa khỏi biểu mẫu.');
  }

  async function addModel(providerId: string) {
    const draft = newModels[providerId];
    setError('');
    if (!draft?.model_name.trim() || !draft.display_name.trim()) {
      setError('Tên mô hình và tên hiển thị là bắt buộc.');
      return;
    }
    await createModel.mutateAsync({
      providerId,
      payload: {
        model_name: draft.model_name.trim(),
        display_name: draft.display_name.trim(),
        enabled: draft.enabled,
        is_default: draft.is_default,
        max_tokens: optionalInt(draft.max_tokens),
        temperature_default: optionalFloat(draft.temperature_default),
        priority: numberValue(draft.priority),
      },
    });
    setNewModels((prev) => ({
      ...prev,
      [providerId]: {
        model_name: '',
        display_name: '',
        enabled: true,
        is_default: false,
        max_tokens: '',
        temperature_default: '',
        priority: '100',
      },
    }));
    setMessage('Đã lưu mô hình.');
  }

  async function saveModel(model: AiModel) {
    const draft = modelEdits[model.id] ?? modelDraftFrom(model);
    setError('');
    await updateModel.mutateAsync({
      modelId: model.id,
      payload: {
        model_name: draft.model_name.trim(),
        display_name: draft.display_name.trim(),
        enabled: draft.enabled,
        is_default: draft.is_default,
        max_tokens: optionalInt(draft.max_tokens),
        temperature_default: optionalFloat(draft.temperature_default),
        priority: numberValue(draft.priority, model.priority),
      },
    });
    setModelEdits((prev) => {
      const next = { ...prev };
      delete next[model.id];
      return next;
    });
    setMessage('Đã cập nhật mô hình.');
  }

  async function runTest() {
    setError('');
    setMessage('');
    const result = await testAi.mutateAsync({
      provider_id: testState.provider_id || null,
      model_id: testState.model_id || null,
      key_id: testState.key_id || null,
      prompt: testState.prompt,
      max_tokens: numberValue(testState.max_tokens, 128),
      temperature: optionalFloat(testState.temperature) ?? 0.2,
    });
    setMessage(`Kiểm tra thành công trong ${result.elapsed_ms} ms: ${result.text}`);
  }

  if (providersQuery.isLoading) {
    return (
      <div>
        <PageHeader title="AI Providers & API Keys" subtitle="Quản lý mã khóa bảo mật và định tuyến mô hình AI." />
        <div className="card"><Skeleton height={260} /></div>
      </div>
    );
  }

  if (providersQuery.error) {
    return (
      <div>
        <PageHeader title="AI Providers & API Keys" subtitle="Quản lý mã khóa bảo mật và định tuyến mô hình AI." />
        <div className="card" style={{ color: 'var(--danger)' }}>
          {(providersQuery.error as Error).message}
          <button className="btn btn-secondary btn-sm" style={{ marginLeft: '0.75rem' }} onClick={() => providersQuery.refetch()}>
            <RefreshCw size={13} /> Retry
          </button>
        </div>
      </div>
    );
  }

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '1rem', marginBottom: '0.875rem' }}>
        <div>
          <div className="page-title">Nhà Cung Cấp AI & API Keys</div>
          <div className="page-subtitle">Lưu trữ khóa bảo mật cục bộ dùng cho định tuyến AI, fallback và thiết lập mô hình mặc định.</div>
        </div>
        <button className="btn btn-primary btn-sm" onClick={() => setShowProviderForm((v) => !v)}>
          <Plus size={14} /> Thêm Nhà Cung Cấp
        </button>
      </div>

      <PageHeader title="Nhà Cung Cấp AI & API Keys" />

      {(message || error || testAi.error) && (
        <div
          style={{
            padding: '0.75rem 1rem',
            borderRadius: 'var(--radius-sm)',
            border: `1px solid ${error || testAi.error ? 'var(--danger)' : 'var(--success)'}`,
            background: error || testAi.error ? 'var(--danger-muted)' : 'var(--success-muted)',
            color: error || testAi.error ? 'var(--danger)' : 'var(--success)',
            marginBottom: '1rem',
            fontSize: '0.8125rem',
          }}
        >
          {error || (testAi.error as Error | null)?.message || message}
        </div>
      )}

      {showProviderForm && (
        <div className="card" style={{ marginBottom: '1.25rem' }}>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1.2fr 1.4fr 0.7fr auto auto', gap: '0.75rem', alignItems: 'end' }}>
            <div>
              <FieldLabel>Nhà cung cấp</FieldLabel>
              <input className="input" value={newProvider.provider} onChange={(e) => setNewProvider((p) => ({ ...p, provider: e.target.value }))} placeholder="openai" />
            </div>
            <div>
              <FieldLabel>Tên hiển thị</FieldLabel>
              <input className="input" value={newProvider.display_name} onChange={(e) => setNewProvider((p) => ({ ...p, display_name: e.target.value }))} placeholder="OpenAI" />
            </div>
            <div>
              <FieldLabel>URL Gốc</FieldLabel>
              <input className="input" value={newProvider.base_url} onChange={(e) => setNewProvider((p) => ({ ...p, base_url: e.target.value }))} placeholder="Tùy chọn" />
            </div>
            <div>
              <FieldLabel>Độ ưu tiên</FieldLabel>
              <input className="input" type="number" value={newProvider.priority} onChange={(e) => setNewProvider((p) => ({ ...p, priority: e.target.value }))} />
            </div>
            {boolToggle(newProvider.enabled, (enabled) => setNewProvider((p) => ({ ...p, enabled })), 'Bật nhà cung cấp')}
            <button className="btn btn-primary btn-sm" onClick={addProvider} disabled={createProvider.isPending}>
              <Save size={13} /> Save
            </button>
          </div>
        </div>
      )}

      <div className="card" style={{ marginBottom: '1.25rem' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: '1rem', alignItems: 'center', marginBottom: '0.875rem' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', fontWeight: 700 }}>
            <Play size={16} /> Kiểm Tra Định Tuyến
          </div>
          <button className="btn btn-secondary btn-sm" onClick={runTest} disabled={testAi.isPending}>
            <Play size={13} /> {testAi.isPending ? 'Đang kiểm tra' : 'Kiểm tra'}
          </button>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '0.75rem', marginBottom: '0.75rem' }}>
          <div>
            <FieldLabel>Nhà cung cấp</FieldLabel>
            <select className="select" value={testState.provider_id} onChange={(e) => setTestState((s) => ({ ...s, provider_id: e.target.value, model_id: '', key_id: '' }))}>
              <option value="">Tự động định tuyến</option>
              {providers.map((provider) => (
                <option key={provider.id} value={provider.id}>{provider.display_name}</option>
              ))}
            </select>
          </div>
          <div>
            <FieldLabel>Mô hình</FieldLabel>
            <select className="select" value={testState.model_id} onChange={(e) => setTestState((s) => ({ ...s, model_id: e.target.value }))}>
              <option value="">Mô hình mặc định đã bật</option>
              {visibleModels.map(({ provider, model }) => (
                <option key={model.id} value={model.id}>{provider.display_name} / {model.model_name}</option>
              ))}
            </select>
          </div>
          <div>
            <FieldLabel>Khóa</FieldLabel>
            <select className="select" value={testState.key_id} onChange={(e) => setTestState((s) => ({ ...s, key_id: e.target.value }))}>
              <option value="">Tự động fallback khóa</option>
              {visibleKeys.map(({ provider, key }) => (
                <option key={key.id} value={key.id}>{provider.display_name} / {key.label} ({key.key_preview})</option>
              ))}
            </select>
          </div>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 100px 100px', gap: '0.75rem' }}>
          <input className="input" value={testState.prompt} onChange={(e) => setTestState((s) => ({ ...s, prompt: e.target.value }))} />
          <input className="input" type="number" value={testState.max_tokens} onChange={(e) => setTestState((s) => ({ ...s, max_tokens: e.target.value }))} title="Max tokens" />
          <input className="input" type="number" step="0.1" value={testState.temperature} onChange={(e) => setTestState((s) => ({ ...s, temperature: e.target.value }))} title="Temperature" />
        </div>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '1.25rem' }}>
        {providers.map((provider) => {
          const providerDraft = providerEdits[provider.id] ?? providerDraftFrom(provider);
          const newKey = newKeys[provider.id] ?? { label: 'Main key', raw_key: '', enabled: true, priority: '100' };
          const newModel = newModels[provider.id] ?? {
            model_name: '',
            display_name: '',
            enabled: true,
            is_default: false,
            max_tokens: '',
            temperature_default: '',
            priority: '100',
          };
          const enabledKeys = provider.keys.filter((key) => key.enabled).length;
          const hasDefaultModel = provider.models.some((model) => model.enabled && model.is_default);
          const needsKey = PROVIDERS_REQUIRING_KEYS.has(provider.provider);

          return (
            <div className="card" key={provider.id}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: '1rem', alignItems: 'flex-start', marginBottom: '1rem' }}>
                <div style={{ display: 'flex', gap: '0.75rem', minWidth: 0 }}>
                  <Bot size={24} style={{ color: 'var(--primary)', flexShrink: 0 }} />
                  <div style={{ minWidth: 0 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' }}>
                      <span style={{ fontWeight: 800, fontSize: '1rem' }}>{provider.display_name}</span>
                      <Badge status={provider.enabled ? 'active' : 'muted'}>{provider.enabled ? 'Enabled' : 'Disabled'}</Badge>
                      <span className="mono" style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }}>{provider.provider}</span>
                    </div>
                    <div style={{ display: 'flex', gap: '1rem', marginTop: '0.35rem', color: 'var(--text-muted)', fontSize: '0.75rem' }}>
                      <span>{provider.keys.length} khóa</span>
                      <span>{provider.models.length} mô hình</span>
                      <span>ưu tiên {provider.priority}</span>
                    </div>
                  </div>
                </div>
                <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
                  {boolToggle(providerDraft.enabled, (enabled) => setProviderEdit(provider.id, { enabled }), 'Bật nhà cung cấp')}
                  <button className="btn btn-secondary btn-sm" onClick={() => saveProvider(provider)} disabled={updateProvider.isPending}>
                    <Save size={13} /> Save
                  </button>
                  <button
                    className="btn btn-danger btn-sm"
                    onClick={() => {
                      if (window.confirm(`Xóa nhà cung cấp ${provider.display_name}? Thao tác này cũng xóa các khóa và mô hình của nó.`)) {
                        deleteProvider.mutate(provider.id);
                      }
                    }}
                  >
                    <Trash2 size={13} /> Delete
                  </button>
                </div>
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1.2fr 1.5fr 0.55fr', gap: '0.75rem', marginBottom: '0.875rem' }}>
                <div>
                  <FieldLabel>Nhà cung cấp</FieldLabel>
                  <input className="input" value={providerDraft.provider} onChange={(e) => setProviderEdit(provider.id, { provider: e.target.value })} />
                </div>
                <div>
                  <FieldLabel>Tên hiển thị</FieldLabel>
                  <input className="input" value={providerDraft.display_name} onChange={(e) => setProviderEdit(provider.id, { display_name: e.target.value })} />
                </div>
                <div>
                  <FieldLabel>URL Gốc</FieldLabel>
                  <input className="input" value={providerDraft.base_url} onChange={(e) => setProviderEdit(provider.id, { base_url: e.target.value })} placeholder="Tùy chọn" />
                </div>
                <div>
                  <FieldLabel>Độ ưu tiên</FieldLabel>
                  <input className="input" type="number" value={providerDraft.priority} onChange={(e) => setProviderEdit(provider.id, { priority: e.target.value })} />
                </div>
              </div>

              <div style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap', marginBottom: '1rem' }}>
                {provider.enabled && needsKey && enabledKeys === 0 && <WarningText>Chưa cấu hình khóa nào được bật</WarningText>}
                {provider.enabled && !hasDefaultModel && <WarningText>Chưa có mô hình mặc định</WarningText>}
              </div>

              <div style={{ borderTop: '1px solid var(--border-subtle)', paddingTop: '1rem', marginBottom: '1.1rem' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', fontWeight: 700, marginBottom: '0.75rem' }}>
                  <KeyRound size={16} /> Keys
                </div>
                <div style={{ overflowX: 'auto' }}>
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>Nhãn</th>
                        <th>Xem trước</th>
                        <th>Đã bật</th>
                        <th>Độ ưu tiên</th>
                        <th>Lần dùng cuối</th>
                        <th>Lỗi cuối</th>
                        <th>Lỗi</th>
                        <th>Thay khóa</th>
                        <th />
                      </tr>
                    </thead>
                    <tbody>
                      {provider.keys.map((key) => {
                        const draft = keyEdits[key.id] ?? keyDraftFrom(key);
                        return (
                          <tr key={key.id}>
                            <td><input className="input" style={cellInputStyle} value={draft.label} onChange={(e) => setKeyEdit(key, { label: e.target.value })} /></td>
                            <td className="mono">{key.key_preview}</td>
                            <td>{boolToggle(draft.enabled, (enabled) => setKeyEdit(key, { enabled }), 'Bật khóa')}</td>
                            <td><input className="input" style={{ ...cellInputStyle, width: 78 }} type="number" value={draft.priority} onChange={(e) => setKeyEdit(key, { priority: e.target.value })} /></td>
                            <td>{formatTime(key.last_used_at)}</td>
                            <td style={{ color: key.last_error ? 'var(--danger)' : 'var(--text-muted)', maxWidth: 220 }}>{key.last_error ?? '-'}</td>
                            <td>{key.failure_count}</td>
                            <td><input className="input" style={cellInputStyle} type="password" value={draft.raw_key} onChange={(e) => setKeyEdit(key, { raw_key: e.target.value })} placeholder="Khóa mới" /></td>
                            <td>
                              <div style={{ display: 'flex', gap: '0.35rem' }}>
                                <button className="btn btn-secondary btn-sm" onClick={() => saveKey(key)}><Save size={12} /></button>
                                <button className="btn btn-danger btn-sm" onClick={() => window.confirm(`Xóa khóa ${key.label}?`) && deleteKey.mutate(key.id)}><Trash2 size={12} /></button>
                              </div>
                            </td>
                          </tr>
                        );
                      })}
                      <tr>
                        <td><input className="input" style={cellInputStyle} value={newKey.label} onChange={(e) => setNewKeyDraft(provider.id, { label: e.target.value })} placeholder="Label" /></td>
                        <td className="mono">Sẽ mã hóa sau khi lưu</td>
                        <td>{boolToggle(newKey.enabled, (enabled) => setNewKeyDraft(provider.id, { enabled }), 'Bật khóa mới')}</td>
                        <td><input className="input" style={{ ...cellInputStyle, width: 78 }} type="number" value={newKey.priority} onChange={(e) => setNewKeyDraft(provider.id, { priority: e.target.value })} /></td>
                        <td colSpan={3} style={{ color: 'var(--text-muted)' }}>Khóa gốc không bao giờ được trả về sau khi lưu</td>
                        <td><input className="input" style={cellInputStyle} type="password" value={newKey.raw_key} onChange={(e) => setNewKeyDraft(provider.id, { raw_key: e.target.value })} placeholder="API key" /></td>
                        <td><button className="btn btn-primary btn-sm" onClick={() => addKey(provider.id)} disabled={createKey.isPending}><Plus size={12} /></button></td>
                      </tr>
                    </tbody>
                  </table>
                </div>
              </div>

              <div style={{ borderTop: '1px solid var(--border-subtle)', paddingTop: '1rem' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', fontWeight: 700, marginBottom: '0.75rem' }}>
                  <Bot size={16} /> Models
                </div>
                <div style={{ overflowX: 'auto' }}>
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>Tên mô hình</th>
                        <th>Tên hiển thị</th>
                        <th>Đã bật</th>
                        <th>Mặc định</th>
                        <th>Độ ưu tiên</th>
                        <th>Token tối đa</th>
                        <th>Temperature</th>
                        <th />
                      </tr>
                    </thead>
                    <tbody>
                      {provider.models.map((model) => {
                        const draft = modelEdits[model.id] ?? modelDraftFrom(model);
                        return (
                          <tr key={model.id}>
                            <td><input className="input" style={cellInputStyle} value={draft.model_name} onChange={(e) => setModelEdit(model, { model_name: e.target.value })} /></td>
                            <td><input className="input" style={cellInputStyle} value={draft.display_name} onChange={(e) => setModelEdit(model, { display_name: e.target.value })} /></td>
                            <td>{boolToggle(draft.enabled, (enabled) => setModelEdit(model, { enabled }), 'Bật mô hình')}</td>
                            <td>{boolToggle(draft.is_default, (is_default) => setModelEdit(model, { is_default }), 'Mô hình mặc định')}</td>
                            <td><input className="input" style={{ ...cellInputStyle, width: 78 }} type="number" value={draft.priority} onChange={(e) => setModelEdit(model, { priority: e.target.value })} /></td>
                            <td><input className="input" style={{ ...cellInputStyle, width: 92 }} type="number" value={draft.max_tokens} onChange={(e) => setModelEdit(model, { max_tokens: e.target.value })} placeholder="Bất kỳ" /></td>
                            <td><input className="input" style={{ ...cellInputStyle, width: 92 }} type="number" step="0.1" value={draft.temperature_default} onChange={(e) => setModelEdit(model, { temperature_default: e.target.value })} placeholder="Gọi API" /></td>
                            <td>
                              <div style={{ display: 'flex', gap: '0.35rem' }}>
                                <button className="btn btn-secondary btn-sm" onClick={() => saveModel(model)}><Save size={12} /></button>
                                <button className="btn btn-danger btn-sm" onClick={() => window.confirm(`Xóa mô hình ${model.model_name}?`) && deleteModel.mutate(model.id)}><Trash2 size={12} /></button>
                              </div>
                            </td>
                          </tr>
                        );
                      })}
                      <tr>
                        <td><input className="input" style={cellInputStyle} value={newModel.model_name} onChange={(e) => setNewModelDraft(provider.id, { model_name: e.target.value })} placeholder="model-name" /></td>
                        <td><input className="input" style={cellInputStyle} value={newModel.display_name} onChange={(e) => setNewModelDraft(provider.id, { display_name: e.target.value })} placeholder="Display" /></td>
                        <td>{boolToggle(newModel.enabled, (enabled) => setNewModelDraft(provider.id, { enabled }), 'Bật mô hình mới')}</td>
                        <td>{boolToggle(newModel.is_default, (is_default) => setNewModelDraft(provider.id, { is_default }), 'Mô hình mới mặc định')}</td>
                        <td><input className="input" style={{ ...cellInputStyle, width: 78 }} type="number" value={newModel.priority} onChange={(e) => setNewModelDraft(provider.id, { priority: e.target.value })} /></td>
                        <td><input className="input" style={{ ...cellInputStyle, width: 92 }} type="number" value={newModel.max_tokens} onChange={(e) => setNewModelDraft(provider.id, { max_tokens: e.target.value })} placeholder="Bất kỳ" /></td>
                        <td><input className="input" style={{ ...cellInputStyle, width: 92 }} type="number" step="0.1" value={newModel.temperature_default} onChange={(e) => setNewModelDraft(provider.id, { temperature_default: e.target.value })} placeholder="Gọi API" /></td>
                        <td><button className="btn btn-primary btn-sm" onClick={() => addModel(provider.id)} disabled={createModel.isPending}><Plus size={12} /></button></td>
                      </tr>
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
