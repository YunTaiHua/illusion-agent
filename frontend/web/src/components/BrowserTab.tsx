/**
 * @fileoverview 浏览器设置卡片（SetupForm「扩展能力」tab）
 *
 * 内置浏览器开关与浏览配置，按运行端分支：
 * - Web/终端端：托管内核、无头模式、代理；
 * - 桌面端：浏览器数据管理（清除全部数据 / 仅清除缓存）。
 *
 * @module BrowserTab
 */

import { useEffect, useRef, useState } from 'react';
import { t, type UiLanguage } from '../i18n';
import { settingsApi, type SettingsResponse } from '../api';
import ToggleSwitch from './ToggleSwitch';
import { TrashIcon } from './icons';
import type { PluginSnapshot } from '../types/protocol';

/** 浏览器配置（settings.browser） */
interface BrowserConfig {
  kernel: string;
  headless: boolean;
  proxy: string;
}

interface BrowserTabProps {
  lang: UiLanguage;
  /** browser-use 插件快照（App 透传；enabled 即内置浏览器开关） */
  browserUse: PluginSnapshot | undefined;
  /** 切换 browser-use 启用状态（WS web_plugin_toggle 热切换） */
  onToggle: (enabled: boolean) => void;
  /** 全局设置写入（WS web_set_setting；browser_* 键） */
  sendSetting: (key: string, value: string | number | boolean) => void;
  /** 运行在桌面壳内 */
  isDesktop: boolean;
}

/** 字段标签样式（与 SetupForm 同款） */
const labelClass = 'text-xs font-medium text-content-secondary mb-1.5';
/** 输入框样式（与 SetupForm 同款） */
const inputClass = 'w-full px-3 py-2 rounded-md bg-surface-card-alt border border-border-light text-content-primary text-sm focus:outline-none focus:border-primary transition-all duration-200';

/** 内核选项 */
const KERNEL_OPTIONS: Array<{ value: string; labelKey: string }> = [
  { value: 'auto', labelKey: 'plugins_kernel_auto' },
  { value: 'chromium', labelKey: 'plugins_kernel_chromium' },
  { value: 'chrome', labelKey: 'plugins_kernel_chrome' },
  { value: 'msedge', labelKey: 'plugins_kernel_msedge' },
];

export function BrowserTab({ lang, browserUse, onToggle, sendSetting, isDesktop }: BrowserTabProps) {
  /** 浏览器配置（settings.browser 回显） */
  const [browser, setBrowser] = useState<BrowserConfig>({ kernel: 'auto', headless: true, proxy: 'auto' });
  /** 代理输入草稿（失焦/回车提交） */
  const [proxyDraft, setProxyDraft] = useState<string | null>(null);
  /** 刚完成的清除操作（短暂反馈） */
  const [cleared, setCleared] = useState<'all' | 'cache' | null>(null);
  const clearedTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  /** 待确认的清除操作（确认弹窗） */
  const [pendingClear, setPendingClear] = useState<'all' | 'cache' | null>(null);

  useEffect(() => {
    let mounted = true;
    settingsApi.get().then((s: SettingsResponse) => {
      if (!mounted || !s.browser) return;
      setBrowser({
        kernel: s.browser.kernel ?? 'auto',
        headless: s.browser.headless ?? true,
        proxy: s.browser.proxy ?? 'auto',
      });
    }).catch(() => undefined);
    return () => { mounted = false; };
  }, []);

  const patchBrowser = (patch: Partial<BrowserConfig>) => {
    setBrowser((prev) => ({ ...prev, ...patch }));
    if (patch.kernel !== undefined) sendSetting('browser_kernel', patch.kernel);
    if (patch.headless !== undefined) sendSetting('browser_headless', patch.headless);
    if (patch.proxy !== undefined) sendSetting('browser_proxy', patch.proxy);
  };

  const commitProxy = () => {
    if (proxyDraft === null) return;
    const value = proxyDraft.trim() || 'auto';
    setProxyDraft(null);
    if (value !== browser.proxy) patchBrowser({ proxy: value });
  };

  const clearBrowserData = async (kind: 'all' | 'cache') => {
    const api = window.illusionDesktop;
    if (kind === 'all') await api?.clearBrowserData?.().catch(() => undefined);
    else await api?.clearBrowserCache?.().catch(() => undefined);
    setCleared(kind);
    if (clearedTimerRef.current) clearTimeout(clearedTimerRef.current);
    clearedTimerRef.current = setTimeout(() => setCleared(null), 2000);
  };

  const enabled = browserUse?.enabled === true;

  return (
    <div className="w-full">
      <div className="rounded-xl border border-border-light bg-surface-card-alt px-4 py-3">
        <div className="flex items-start gap-3">
          <div className="flex-1 min-w-0">
            <div className="text-sm font-medium text-content-primary">{t(lang, 'browser_builtin_title')}</div>
            <div className="text-xs text-content-secondary mt-0.5 leading-relaxed">{t(lang, 'browser_tab_subtitle')}</div>
          </div>
          <ToggleSwitch
            checked={enabled}
            onChange={onToggle}
            label="browser-use"
            title={enabled ? t(lang, 'plugin_disable_hint') : t(lang, 'plugin_enable_hint')}
          />
        </div>

        {/* 浏览配置：Web/终端端=托管内核与运行方式；桌面端=浏览器数据管理 */}
        {enabled && !isDesktop && (
          <div className="mt-3 pt-3 border-t border-border-light flex flex-col gap-3">
            <div>
              <div className={labelClass}>{t(lang, 'plugins_kernel_label')}</div>
              <div className="flex flex-wrap gap-1.5">
                {KERNEL_OPTIONS.map((opt) => (
                  <button
                    key={opt.value}
                    onClick={() => patchBrowser({ kernel: opt.value })}
                    className={`px-2.5 py-1 rounded-md text-xs transition-colors cursor-pointer ${
                      browser.kernel === opt.value
                        ? 'bg-primary text-white'
                        : 'text-content-secondary hover:bg-surface-hover'
                    }`}
                  >
                    {t(lang, opt.labelKey)}
                  </button>
                ))}
              </div>
            </div>
            <label className="flex items-center justify-between gap-3 cursor-pointer">
              <span className="text-xs text-content-secondary">{t(lang, 'plugins_headless_label')}</span>
              <ToggleSwitch checked={browser.headless} onChange={(v) => patchBrowser({ headless: v })} label="headless" />
            </label>
            <div>
              <div className={labelClass}>{t(lang, 'plugins_proxy_label')}</div>
              <input
                value={proxyDraft ?? browser.proxy}
                onChange={(e) => setProxyDraft(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') commitProxy(); }}
                onBlur={commitProxy}
                placeholder="auto / off / http://127.0.0.1:7890"
                spellCheck={false}
                className={inputClass}
              />
              <div className="text-[11px] text-content-disabled mt-1">{t(lang, 'browser_proxy_hint')}</div>
            </div>
          </div>
        )}
        {enabled && isDesktop && (
          <div className="mt-3 pt-3 border-t border-border-light flex flex-col gap-3">
            <div className="flex items-center justify-between gap-3">
              <div>
                <div className="text-xs font-medium text-content-primary">{t(lang, 'browser_data_clear')}</div>
                <div className="text-[11px] text-content-disabled mt-0.5">{t(lang, 'browser_data_clear_hint')}</div>
              </div>
              <button
                onClick={() => setPendingClear('all')}
                title={cleared === 'all' ? t(lang, 'browser_data_cleared') : t(lang, 'browser_data_clear')}
                aria-label={t(lang, 'browser_data_clear')}
                className="shrink-0 w-7 h-7 flex items-center justify-center rounded-md text-content-secondary border border-border-light glass-option-hover hover:text-content-primary transition-colors cursor-pointer"
              >
                <TrashIcon className="w-3.5 h-3.5" />
              </button>
            </div>
            <div className="flex items-center justify-between gap-3">
              <div>
                <div className="text-xs font-medium text-content-primary">{t(lang, 'browser_cache_clear')}</div>
                <div className="text-[11px] text-content-disabled mt-0.5">{t(lang, 'browser_cache_clear_hint')}</div>
              </div>
              <button
                onClick={() => setPendingClear('cache')}
                title={cleared === 'cache' ? t(lang, 'browser_data_cleared') : t(lang, 'browser_cache_clear')}
                aria-label={t(lang, 'browser_cache_clear')}
                className="shrink-0 w-7 h-7 flex items-center justify-center rounded-md text-content-secondary border border-border-light glass-option-hover hover:text-content-primary transition-colors cursor-pointer"
              >
                <TrashIcon className="w-3.5 h-3.5" />
              </button>
            </div>
          </div>
        )}
      </div>

      {/* 清除确认弹窗 */}
      {pendingClear && (
        <div className="fixed inset-0 z-[80] flex items-center justify-center">
          <div className="absolute inset-0" onClick={() => setPendingClear(null)} />
          <div className="relative bg-surface-card border border-border-light rounded-2xl shadow-card p-5 w-[320px] max-w-[90vw] animate-scale-in">
            <div className="text-sm font-semibold text-content-primary">
              {t(lang, pendingClear === 'all' ? 'browser_data_clear' : 'browser_cache_clear')}
            </div>
            <div className="text-xs text-content-secondary mt-1.5 leading-relaxed">
              {t(lang, 'browser_data_confirm_hint')}
            </div>
            <div className="flex justify-end gap-2 mt-4">
              <button
                onClick={() => setPendingClear(null)}
                className="px-3 py-1.5 text-xs text-content-secondary rounded-lg border border-border-light glass-option-hover hover:text-content-primary transition-colors cursor-pointer"
              >
                {t(lang, 'cancel')}
              </button>
              <button
                onClick={() => {
                  const kind = pendingClear;
                  setPendingClear(null);
                  if (kind) void clearBrowserData(kind);
                }}
                className="px-3 py-1.5 text-xs text-white bg-danger hover:bg-danger-hover rounded-lg transition-colors cursor-pointer"
              >
                {t(lang, 'confirm')}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
