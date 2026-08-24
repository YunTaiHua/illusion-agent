/**
 * @fileoverview React 错误边界组件
 *
 * 捕获子组件树中的渲染错误，防止整个应用白屏崩溃。
 * 显示友好的错误提示和重新加载按钮。
 * 使用 navigator.language 做最佳努力的语言检测（class 组件无法用 hooks）。
 *
 * @module ErrorBoundary
 */

import { Component, type ReactNode } from 'react';
import { normalizeLanguage } from '../i18n';

/** ErrorBoundary 专用的轻量 i18n（class 组件无法用 hooks，基于浏览器语言检测） */
const EB_TEXT: Record<string, { title: string; detail: string; retry: string; reload: string }> = {
  'zh-CN': { title: '页面渲染出错', detail: '发生了未知错误', retry: '重试', reload: '重新加载页面' },
  'en': { title: 'Rendering Error', detail: 'An unknown error occurred', retry: 'Retry', reload: 'Reload Page' },
};

function ebText(): { title: string; detail: string; retry: string; reload: string } {
  const lang = normalizeLanguage(navigator.language);
  return EB_TEXT[lang] ?? EB_TEXT['en']!;
}

/**
 * ErrorBoundary 组件属性接口
 */
interface ErrorBoundaryProps {
  /** 子组件 */
  children: ReactNode;
}

/**
 * ErrorBoundary 组件状态接口
 */
interface ErrorBoundaryState {
  /** 是否有错误 */
  hasError: boolean;
  /** 错误信息 */
  error: Error | null;
}

/**
 * 错误边界组件
 *
 * 捕获子组件树中的渲染错误，显示友好的错误提示，
 * 防止整个应用白屏崩溃。
 */
export default class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  constructor(props: ErrorBoundaryProps) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: React.ErrorInfo): void {
    console.error('[ErrorBoundary]', error, errorInfo);
  }

  handleReload = (): void => {
    window.location.reload();
  };

  handleReset = (): void => {
    this.setState({ hasError: false, error: null });
  };

  render(): ReactNode {
    if (this.state.hasError) {
      const t = ebText();
      return (
        <div className="flex items-center justify-center h-screen bg-surface-main">
          <div className="text-center max-w-md px-6">
            <div className="text-4xl mb-4 flex justify-center text-amber-500">
              <svg width="48" height="48" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
                <path d="M12 2L1 21h22L12 2zm1 15h-2v-2h2v2zm0-4h-2V9h2v4z" />
              </svg>
            </div>
            <h2 className="text-lg font-semibold text-content-primary mb-2">{t.title}</h2>
            <p className="text-sm text-content-secondary mb-4">
              {this.state.error?.message || t.detail}
            </p>
            <div className="flex gap-3 justify-center">
              <button
                onClick={this.handleReset}
                className="px-4 py-2 text-sm text-content-secondary hover:bg-surface-hover rounded-lg transition-colors cursor-pointer border border-border-light"
              >
                {t.retry}
              </button>
              <button
                onClick={this.handleReload}
                className="px-4 py-2 text-sm text-white bg-primary hover:bg-primary-hover rounded-lg transition-colors cursor-pointer"
              >
                {t.reload}
              </button>
            </div>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}
