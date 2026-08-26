/**
 * @fileoverview 自定义数字输入模态组件
 *
 * Web 前端的轻量模态对话框，现用于 /rename 分支的重命名文本输入
 * （文本模式；数字模式由 terminal 端的 max-tokens/context-window custom 分支复用）。
 *
 * 视觉风格与 AgentWizardForm 表单保持一致：
 * - bg-surface-card 实色卡片 + border + shadow-card
 * - rounded-2xl 圆角
 * - bg-primary / bg-primary-hover 主按钮
 * - glass-option-hover 次按钮
 *
 * @module CustomInputModal
 */

import { useEffect, useRef, useState } from 'react';
import { t, type UiLanguage } from '../i18n';

/**
 * CustomInputModal 组件属性接口
 */
interface CustomInputModalProps {
  /** 当前 UI 语言 */
  lang: UiLanguage;
  /** 提示文案 */
  prompt: string;
  /** 提交回调（value 为数字字符串或文本） */
  onSubmit: (value: string) => void;
  /** 取消回调 */
  onCancel: () => void;
  /** 校验失败时的错误文案（可选，默认使用通用文案） */
  invalidMessage?: string;
  /** 输入模式：numeric（正整数校验）或 text（非空校验） */
  mode?: 'numeric' | 'text';
}

/**
 * 自定义数字输入模态组件
 *
 * 弹出居中对话框，要求用户输入一个正整数。
 * - 挂载时自动聚焦输入框
 * - Enter 提交，Escape 取消
 * - 点击遮罩取消
 * - 仅接受正整数（^\d+$ 且 > 0）
 *
 * @param props - 组件属性
 * @returns 返回模态对话框的 JSX 元素
 */
export function CustomInputModal({ lang, prompt, onSubmit, onCancel, invalidMessage, mode = 'numeric' }: CustomInputModalProps) {
  const [value, setValue] = useState('');
  const [error, setError] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  // 挂载时自动聚焦
  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const handleSubmit = () => {
    if (mode === 'text') {
      if (value.trim()) {
        onSubmit(value.trim());
      } else {
        setError(true);
      }
    } else {
      if (/^\d+$/.test(value) && parseInt(value, 10) > 0) {
        onSubmit(value);
      } else {
        setError(true);
      }
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      handleSubmit();
    } else if (e.key === 'Escape') {
      e.preventDefault();
      onCancel();
    }
  };

  // 通用 confirm / maxTokensInvalid 文案已由 i18n 提供
  const confirmLabel = t(lang, 'confirm');
  const defaultInvalidMessage = t(lang, 'maxTokensInvalid');

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30" onClick={onCancel}>
      <div
        className="bg-surface-card rounded-2xl border border-border-light shadow-card p-6 w-80 max-w-[90vw] animate-fade-in-up"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="text-sm text-content-primary mb-3">{prompt}</div>
        <input
          ref={inputRef}
          type="text"
          inputMode={mode === 'numeric' ? 'numeric' : 'text'}
          className="w-full px-3 py-2 rounded-md bg-surface-card-alt border border-border-light text-content-primary text-sm focus:outline-none focus:border-primary focus:shadow-glow transition-all duration-200"
          value={value}
          onChange={(e) => {
            setValue(e.target.value);
            setError(false);
          }}
          onKeyDown={handleKeyDown}
        />
        {error && (
          <div className="text-xs text-red-500 mt-2">
            {invalidMessage || defaultInvalidMessage}
          </div>
        )}
        <div className="flex justify-end gap-2 mt-4">
          <button
            className="px-3 py-1.5 text-sm text-content-secondary glass-option-hover rounded-md cursor-pointer"
            onClick={onCancel}
          >
            {t(lang, 'cancel')}
          </button>
          <button
            className="px-3 py-1.5 text-sm bg-primary text-white rounded-md hover:bg-primary-hover cursor-pointer"
            onClick={handleSubmit}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
