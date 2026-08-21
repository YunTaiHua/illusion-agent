/**
 * @fileoverview 模态卡片组件
 *
 * Web 前端的模态对话框组件，支持：
 * - 权限请求卡片（拒绝/允许一次/本次会话允许）
 * - 问答卡片（单选/多选/自定义输入）
 *
 * @module ModalCard
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkSuperscript from '../remarkSuperscript';
import rehypeHighlight from 'rehype-highlight';
import rehypeRaw from 'rehype-raw';
import { t, type UiLanguage } from '../i18n';
import { toolDisplayName } from '../utils/toolDisplayName';

/**
 * 问题选项接口
 */
interface QuestionOption {
  /** 选项标签 */
  label: string;
  /** 选项描述 */
  description?: string;
}

/**
 * 问题项接口
 */
interface QuestionItem {
  /** 问题文本 */
  question: string;
  /** 问题标题（可选） */
  header?: string;
  /** 选项列表（可选） */
  options?: QuestionOption[];
  /** 是否多选（可选） */
  multiSelect?: boolean;
  /** 禁用手动输入（可选，如沙箱权限对话框） */
  noCustomInput?: boolean;
}

// ---- 权限请求卡片 ----

/**
 * 权限卡片组件属性接口
 */
interface PermissionCardProps {
  /** 模态对话框配置 */
  modal: Record<string, unknown>;
  /** 当前 UI 语言 */
  lang: UiLanguage;
  /** 响应回调函数 */
  onRespond: (requestId: string, allowed: boolean, sessionAllow: boolean, toolName: string) => void;
}

/**
 * 权限请求卡片组件
 *
 * 显示工具执行权限请求，用户可以选择拒绝、允许一次或本次会话允许。
 *
 * @param props - 组件属性
 * @returns 返回权限卡片的 JSX 元素
 */
export function PermissionCard({ modal, lang, onRespond }: PermissionCardProps) {
  const toolName = String(modal.tool_name ?? 'tool');
  // 显示用友好名；onRespond 回调仍传原始 toolName 给后端识别
  const displayToolName = toolDisplayName(toolName);
  const reason = modal.reason ? String(modal.reason) : null;
  const requestId = String(modal.request_id ?? '');
  // 高危操作（如 rm / git reset --hard）只提供两选项（允许一次 / 拒绝），不可会话级豁免
  const highRisk = modal.high_risk === true;

  // 权限确认选项：仿照问题卡片，把选择项渲染为整行大按钮
  // 顺序：允许一次 / 本次会话允许 / 拒绝
  // 高危操作隐藏"本次会话允许"，仅保留允许一次 / 拒绝
  const permissionOptions = [
    {
      key: 'allow' as const,
      label: t(lang, 'allow'),
      description: lang === 'zh-CN' ? '仅允许此次' : 'Allow this once',
      onClick: () => onRespond(requestId, true, false, toolName),
    },
    ...(highRisk
      ? []
      : [
          {
            key: 'session_allow' as const,
            label: t(lang, 'session_allow'),
            description: lang === 'zh-CN' ? '本次会话内自动允许' : 'Allow automatically for this session',
            onClick: () => onRespond(requestId, true, true, toolName),
          },
        ]),
    {
      key: 'deny' as const,
      label: t(lang, 'deny'),
      description: lang === 'zh-CN' ? '拒绝此次操作' : 'Deny this operation',
      onClick: () => onRespond(requestId, false, false, toolName),
    },
  ];

  return (
    <div className="my-3 rounded-2xl glass-surface overflow-hidden">
      {/* 标题区：警告图标 + 权限确认内容（对齐问答卡片标题） */}
      <div className="px-4 py-3">
        <div className="flex items-center gap-2 mb-1">
          <svg className="w-4 h-4 text-amber-500 shrink-0" viewBox="0 0 16 16" fill="currentColor">
            <path d="M8.982 1.566a1.13 1.13 0 0 0-1.96 0L.165 13.233c-.457.778.091 1.767.98 1.767h13.713c.889 0 1.438-.99.98-1.767L8.982 1.566zM8 5c.535 0 .954.462.9.995l-.35 3.507a.552.552 0 0 1-1.1 0L7.1 5.995A.905.905 0 0 1 8 5zm.002 6a1 1 0 1 1 0 2 1 1 0 0 1 0-2z" />
          </svg>
          <span className="text-sm font-medium text-content-primary">
            {lang === 'zh-CN' ? '允许使用工具 ' : 'Allow '}
            <span className="font-mono font-medium text-primary">{displayToolName}</span>
            <span>?</span>
          </span>
        </div>
        {reason && (
          <div className="text-xs text-content-secondary mt-0.5 leading-relaxed">{reason}</div>
        )}
      </div>

      {/* 选项区：整行大按钮（对齐问答卡片选项样式） */}
      <div className="px-4 pb-4 space-y-1.5">
        {permissionOptions.map((opt) => (
          <button
            key={opt.key}
            onClick={opt.onClick}
            className={`w-full text-left px-3 py-2.5 rounded-lg text-sm transition-colors cursor-pointer flex items-center gap-2.5 ${
              opt.key === 'allow'
                ? 'glass-option-active'
                : 'border border-transparent glass-option-hover'
            }`}
          >
            <span className="mt-0.5 w-4 h-4 rounded-full border shrink-0 flex items-center justify-center">
              <span className={`w-2 h-2 rounded-full ${opt.key === 'allow' ? 'bg-primary' : 'bg-transparent'}`} />
            </span>
            <div className="flex-1 min-w-0">
              <div className={`text-sm font-medium ${opt.key === 'allow' ? 'text-primary' : 'text-content-primary'}`}>
                {opt.label}
              </div>
              <div className="text-xs text-content-disabled mt-0.5">{opt.description}</div>
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}

// ---- 问题卡片 ----

/**
 * 问题卡片组件属性接口
 */
interface QuestionCardProps {
  /** 模态对话框配置 */
  modal: Record<string, unknown>;
  /** 当前 UI 语言 */
  lang: UiLanguage;
  /** 响应回调函数 */
  onRespond: (requestId: string, answer: string) => void;
}

/**
 * 问答卡片组件
 *
 * 显示问题并支持单选、多选或自定义输入回答。
 *
 * @param props - 组件属性
 * @returns 返回问答卡片的 JSX 元素
 */
export function QuestionCard({ modal, lang, onRespond }: QuestionCardProps) {
  const requestId = String(modal.request_id ?? '');
  const questions: QuestionItem[] = Array.isArray(modal.questions) ? (modal.questions as QuestionItem[]) : [];
  // ---- 多问题状态 ----
  const [currentIndex, setCurrentIndex] = useState(0);
  const [multiAnswers, setMultiAnswers] = useState<Record<string, string>>({});
  const isMultiQuestion = questions.length > 1;
  const currentQuestion = questions.length > 0 ? (questions[currentIndex] ?? questions[0]!) : null;
  const options = currentQuestion?.options ?? [];
  // 过滤掉LLM返回的"其他"选项，保留工具自动添加的
  const filteredOptions = useMemo(() => options.filter((opt) => {
    const lbl = opt.label.toLowerCase();
    return !(lbl === 'other' || lbl === '其他' || lbl.startsWith('other') || lbl.startsWith('其他'));
  }), [options]);
  const hasOptions = filteredOptions.length > 0;
  const isMultiSelect = currentQuestion?.multiSelect === true && hasOptions;
  const noCustomInput = currentQuestion?.noCustomInput === true;
  /** "其他"选项在 filteredOptions 之后的索引 */
  const otherIdx = filteredOptions.length;

  // 按题索引持久化多选状态（切换问题不丢失）
  const [allSelectedIndices, setAllSelectedIndices] = useState<Record<number, Set<number>>>({});
  const selectedIndices = allSelectedIndices[currentIndex] ?? new Set<number>();
  const setSelectedIndices = (updater: (prev: Set<number>) => Set<number>) => {
    setAllSelectedIndices((prev) => ({
      ...prev,
      [currentIndex]: updater(prev[currentIndex] ?? new Set<number>()),
    }));
  };
  /** "其他"选项的输入内容（按题索引持久化） */
  const [allOtherText, setAllOtherText] = useState<Record<number, string>>({});
  const otherText = allOtherText[currentIndex] ?? '';
  /** 已跳过的题目标头集合（跳过的问题不进入提交结果，提交按钮按其计数） */
  const [skippedHeaders, setSkippedHeaders] = useState<Set<string>>(new Set());
  /** 单问题多选：当前是否有可提交的内容（勾选了普通选项或输入了"其他"文本） */
  const canSubmitMulti = filteredOptions.some((_, i) => selectedIndices.has(i)) || otherText.trim().length > 0;
  const setOtherText = (updater: ((prev: string) => string) | string) => {
    setAllOtherText((prev) => ({
      ...prev,
      [currentIndex]: typeof updater === 'function' ? updater(prev[currentIndex] ?? '') : updater,
    }));
  };
  /** "其他"选项是否聚焦（输入框可见） */
  const [isOtherFocused, setIsOtherFocused] = useState(false);
  /** 单问题单选：当前选中的普通选项索引（null 表示未选） */
  const [singleSelectedIdx, setSingleSelectedIdx] = useState<number | null>(null);
  const otherInputRef = useRef<HTMLInputElement>(null);
  /** 问题卡片根元素引用，用于单问题的失焦提交 */
  const cardRef = useRef<HTMLDivElement>(null);
  /**
   * 已提交守卫：focusout 冒泡会让"其他"输入框失焦与卡片失焦在同一事件中
   * 各触发一次提交，若无守卫会发出重复 question_response（后端 future 已
   * resolve 后再次 set_result 抛 InvalidStateError）。request_id 变化时重置。
   */
  const submittedRef = useRef(false);
  /**
   * 卡片内点击标记：点击卡片内不可聚焦区域（如 plan 文本）时焦点落到 body，
   * relatedTarget 判断会误判为"失焦到卡片外"。onPointerDown 捕获阶段记录
   * 最近一次点击是否发生在卡片内，失焦提交时据此放行。
   */
  const clickedInsideRef = useRef(false);

  // 切换问题时恢复"其他"输入框的聚焦/显示状态：
  // 若该题已有"其他"输入内容（allOtherText 持久化），则显示输入框与选中态，
  // 否则收起。这样回到已填"其他"的问题时，界面能正确回显勾选与文本。
  useEffect(() => {
    const persistedOther = allOtherText[currentIndex]?.trim() ?? '';
    setIsOtherFocused(persistedOther.length > 0);
  }, [currentIndex, allOtherText]);

  // 单选已选答案（从 multiAnswers 回读）
  const currentHeader = currentQuestion?.header ?? `Q${currentIndex + 1}`;
  const singleSelectAnswer = !isMultiSelect && isMultiQuestion ? multiAnswers[currentHeader] : null;
  /** 单问题单选：当前答案（普通选项的 `序号. 标签` 格式或"其他"文本），null 表示无答案 */
  const singleAnswer =
    singleSelectedIdx !== null && singleSelectedIdx < filteredOptions.length
      ? `${singleSelectedIdx + 1}. ${filteredOptions[singleSelectedIdx]!.label}`
      : otherText.trim() || null;

  // 多选：根据当前选中集合即时计算答案。
  // - 多问题多选：选中即时写入 multiAnswers（无确认按钮，最后统一提交）
  // - 单问题多选：选中只更新本地勾选状态，待失焦时统一提交（避免选一个就被提交）
  /** 记录某题答案并清除该题的跳过标记（回头作答已跳过的问题时恢复提交计数） */
  const recordAnswer = useCallback((header: string, value: string) => {
    setMultiAnswers((prev) => ({ ...prev, [header]: value }));
    setSkippedHeaders((prev) => {
      if (!prev.has(header)) return prev;
      const next = new Set(prev);
      next.delete(header);
      return next;
    });
  }, []);

  const commitMultiSelect = useCallback(
    (selected: Set<number>) => {
      const labels = filteredOptions
        .filter((_, i) => selected.has(i))
        .map((o) => o.label);
      // 选中了"其他"且有输入内容，加入结果
      if (selected.has(otherIdx) && otherText.trim()) {
        labels.push(otherText.trim());
      }
      if (labels.length === 0) {
        // 空选：多问题下删除该 key 以保持 Submit 按钮可见性语义正确
        if (isMultiQuestion) {
          setMultiAnswers((prev) => {
            const next = { ...prev };
            delete next[currentHeader];
            return next;
          });
        }
        return;
      }
      if (isMultiQuestion) {
        // 多问题：选中即时记入，无确认按钮
        recordAnswer(currentHeader, JSON.stringify(labels));
      }
      // 单问题多选：不在此提交，交由卡片失焦时统一提交
    },
    [filteredOptions, otherIdx, otherText, currentHeader, isMultiQuestion, recordAnswer],
  );

  // 单问题多选失焦提交：焦点离开问题卡片时，把当前全部选中项提交给后端
  const submitSingleMultiSelect = useCallback(() => {
    if (!isMultiSelect || isMultiQuestion) return;
    const labels = filteredOptions
      .filter((_, i) => selectedIndices.has(i))
      .map((o) => o.label);
    if (selectedIndices.has(otherIdx) && otherText.trim()) {
      labels.push(otherText.trim());
    }
    if (labels.length === 0) return;
    onRespond(requestId, JSON.stringify({ [currentHeader]: labels }));
  }, [isMultiSelect, isMultiQuestion, filteredOptions, selectedIndices, otherIdx, otherText, requestId, onRespond, currentHeader]);

  // 单问题单选提交：由提交按钮或卡片失焦触发，提交当前答案（普通选项优先，否则"其他"文本）
  const submitSingleSelect = useCallback(() => {
    if (isMultiSelect || isMultiQuestion) return;
    if (!singleAnswer || submittedRef.current) return;
    submittedRef.current = true;
    onRespond(requestId, singleAnswer);
  }, [isMultiSelect, isMultiQuestion, singleAnswer, requestId, onRespond]);

  // 单问题多选提交：同样加守卫，防止失焦路径（输入框 blur + 卡片 blur 冒泡）双触发
  const guardedSubmitSingleMultiSelect = useCallback(() => {
    if (submittedRef.current) return;
    submittedRef.current = true;
    submitSingleMultiSelect();
  }, [submitSingleMultiSelect]);

  // 新模态框（request_id 变化）到来时重置提交守卫
  useEffect(() => {
    submittedRef.current = false;
  }, [requestId]);

  const handleOptionClick = useCallback(
    (idx: number, label: string) => {
      if (isMultiSelect) {
        if (idx === otherIdx) {
          // 多选"其他"：切换选中并聚焦输入框
          setSelectedIndices((prev) => {
            const next = new Set(prev);
            if (next.has(idx)) {
              next.delete(idx);
              setIsOtherFocused(false);
              setOtherText('');
              commitMultiSelect(next);
            } else {
              next.add(idx);
              setIsOtherFocused(true);
              setTimeout(() => otherInputRef.current?.focus(), 0);
              // 选中"其他"暂不提交——需等用户输入文本后由 handleOtherSubmit 提交
            }
            return next;
          });
          return;
        }
        setSelectedIndices((prev) => {
          const next = new Set(prev);
          if (next.has(idx)) next.delete(idx); else next.add(idx);
          // 选中即时生效
          commitMultiSelect(next);
          return next;
        });
        return;
      }
      // 单选"其他"选项：取消普通选项选中并聚焦输入框
      if (idx === otherIdx) {
        setSingleSelectedIdx(null);
        setIsOtherFocused(true);
        setTimeout(() => otherInputRef.current?.focus(), 0);
        return;
      }
      // 单选
      if (isMultiQuestion) {
        recordAnswer(currentHeader, `${idx + 1}. ${label}`);
      } else {
        // 单问题单选：更新选中状态并收起"其他"输入框，由提交按钮或失焦提交
        setSingleSelectedIdx(idx);
        setIsOtherFocused(false);
        setOtherText('');
      }
    },
    [isMultiSelect, otherIdx, isMultiQuestion, currentHeader, commitMultiSelect, recordAnswer],
  );

  // 多选"其他"输入回车时提交：
  // - 单问题多选：把"其他"勾选并触发失焦式提交
  // - 多问题多选：写入 multiAnswers（统一格式）
  const handleMultiConfirm = useCallback(() => {
    if (!isMultiQuestion) {
      guardedSubmitSingleMultiSelect();
    } else {
      commitMultiSelect(selectedIndices);
    }
  }, [isMultiQuestion, selectedIndices, commitMultiSelect, guardedSubmitSingleMultiSelect]);

  const handleOtherSubmit = useCallback(() => {
    if (isMultiSelect) {
      handleMultiConfirm();
      return;
    }
    // 单选"其他"提交
    if (isMultiQuestion) {
      const text = otherText.trim();
      if (!text) return;
      recordAnswer(currentHeader, text);
    } else {
      // 单问题单选：统一由 submitSingleSelect 提交（普通选项或"其他"文本）
      submitSingleSelect();
    }
  }, [isMultiSelect, isMultiQuestion, otherText, currentHeader, handleMultiConfirm, submitSingleSelect, recordAnswer]);

  /**
   * 跳过当前问题。
   * - 单问题：直接提交空答案（后端返回 "(no response)"）
   * - 多问题：标记当前题为"已跳过"并前进；全部作答/跳过后方可提交
   *   （跳过的问题不写入答案，提交按钮按其计数显示）
   */
  const handleSkip = useCallback(() => {
    if (isMultiQuestion) {
      // 若当前题已作答，先移除其答案再标记跳过，避免"已作答 + 已跳过"
      // 重复计数导致提交按钮消失（与 recordAnswer 的对称语义）。
      setSkippedHeaders((prev) => {
        const next = new Set(prev);
        next.add(currentHeader);
        return next;
      });
      setMultiAnswers((prev) => {
        if (!(currentHeader in prev)) return prev;
        const next = { ...prev };
        delete next[currentHeader];
        return next;
      });
      if (currentIndex < questions.length - 1) {
        setCurrentIndex(currentIndex + 1);
      }
    } else {
      if (!submittedRef.current) {
        submittedRef.current = true;
        onRespond(requestId, '');
      }
    }
  }, [isMultiQuestion, skippedHeaders, currentHeader, currentIndex, questions.length, submittedRef, requestId, onRespond]);

  const questionText = currentQuestion?.question ?? String(modal.question ?? 'Question');
  const hintText = isMultiSelect
    ? (lang === 'zh-CN' ? '选择所有适用项' : 'Select all that apply')
    : (lang === 'zh-CN' ? '选择一项' : 'Select one');

  return (
    <div
      ref={cardRef}
      tabIndex={!isMultiQuestion ? -1 : undefined}
      onPointerDownCapture={() => {
        // 记录最近一次指针按下是否发生在卡片内：点击 plan 文本等不可聚焦
        // 区域时焦点会落到 body，relatedTarget 无法区分内外，需此标记辅助
        clickedInsideRef.current = true;
      }}
      onBlur={(e) => {
        // 单问题失焦提交：焦点离开问题卡片时提交当前答案（单选或多选）
        if (isMultiQuestion) return;
        // relatedTarget 为新聚焦元素；若它仍在卡片内，则不算失焦
        const next = e.relatedTarget as Node | null;
        if (next && cardRef.current?.contains(next)) return;
        // 焦点落到 body（点击卡片内不可聚焦区域或外部）：以最近一次指针
        // 按下位置为准——在卡片内则不算失焦，不提交
        if (clickedInsideRef.current) {
          clickedInsideRef.current = false;
          return;
        }
        if (isMultiSelect) {
          guardedSubmitSingleMultiSelect();
        } else {
          submitSingleSelect();
        }
      }}
      className={`my-3 rounded-2xl glass-surface overflow-hidden ${
        !isMultiQuestion ? 'outline-none' : ''
      }`}
    >
      {typeof modal.plan === 'string' && modal.plan && (
        <div className="px-4 pt-3">
          <div className="border border-info/40 rounded-lg px-3 py-2.5 bg-info/5 max-h-80 overflow-y-auto">
            <div className="text-info font-semibold text-sm mb-2 flex items-center gap-1.5">
              <span>📝</span>
              <span>{t(lang, 'planReview')}</span>
            </div>
            <div className="text-sm prose prose-sm max-w-none text-content-primary select-text">
              <ReactMarkdown remarkPlugins={[remarkGfm, remarkSuperscript]} rehypePlugins={[rehypeHighlight, rehypeRaw]}>
                {modal.plan}
              </ReactMarkdown>
            </div>
          </div>
        </div>
      )}
      <div className="px-4 py-3">
        {/* Tab 导航栏 — 仅多问题时显示 */}
        {isMultiQuestion && (
          <div className="flex items-center gap-1 mb-2 overflow-x-auto">
            {questions.map((q, idx) => {
              const headerLabel = q.header ?? `Q${idx + 1}`;
              const isActive = idx === currentIndex;
              const isAnswered = headerLabel in multiAnswers;
              return (
                <button
                  key={idx}
                  onClick={() => setCurrentIndex(idx)}
                  className={`px-2 py-0.5 rounded text-xs font-medium transition-colors whitespace-nowrap cursor-pointer ${
                    isActive
                      ? 'bg-primary text-white'
                      : isAnswered
                        ? 'bg-primary-light text-primary border border-primary/20'
                        : 'glass-option-hover text-content-secondary'
                  }`}
                >
                  {isAnswered && !isActive && <span className="mr-1">✓</span>}
                  {headerLabel}
                </button>
              );
            })}
          </div>
        )}
        <div className="text-sm font-medium text-content-primary">{questionText}</div>
        {hasOptions && (
          <div className="text-xs text-content-disabled mt-0.5">{hintText}</div>
        )}
      </div>

      <div className="px-4 py-3">
        {typeof modal.tool_name === 'string' && modal.tool_name && (
          <div className="text-xs text-content-secondary mb-3">
            Tool: <span className="font-mono text-primary">{toolDisplayName(modal.tool_name)}</span>
          </div>
        )}

        {hasOptions ? (
          <div className="space-y-1.5 mb-3">
            {filteredOptions.map((opt, i) => {
              // 多选：从持久化状态读取；单选：多问题从 multiAnswers 回读，单问题从 singleSelectedIdx 读取
              const isSelected = isMultiSelect
                ? selectedIndices.has(i)
                : isMultiQuestion
                  ? singleSelectAnswer === `${i + 1}. ${opt.label}`
                  : singleSelectedIdx === i;
              return (
                <button
                  key={i}
                  onClick={() => handleOptionClick(i, opt.label)}
                  className={`w-full text-left px-3 py-2.5 rounded-lg text-sm transition-colors cursor-pointer flex items-start gap-2.5 ${
                    isSelected
                      ? 'glass-option-active'
                      : 'border border-transparent glass-option-hover'
                  }`}
                >
                  {isMultiSelect ? (
                    <span className={`mt-0.5 w-4 h-4 rounded border flex items-center justify-center shrink-0 text-xs transition-colors ${
                      isSelected ? 'bg-primary border-primary text-white' : 'border-border-light'
                    }`}>
                      {isSelected ? '✓' : ''}
                    </span>
                  ) : (
                    <span className={`mt-0.5 w-4 h-4 rounded-full border shrink-0 flex items-center justify-center ${
                      isSelected ? 'border-primary' : 'border-border-light'
                    }`}>
                      <span className={`w-2 h-2 rounded-full ${isSelected ? 'bg-primary' : 'bg-transparent'}`} />
                    </span>
                  )}
                  <div className="flex-1 min-w-0">
                    <div className={`text-sm font-medium ${isSelected ? 'text-primary' : 'text-content-primary'}`}>{opt.label}</div>
                    {opt.description && (
                      <div className="text-xs text-content-disabled mt-0.5">{opt.description}</div>
                    )}
                  </div>
                </button>
              );
            })}
            {/* "其他"选项：内联输入框，与普通选项格式一致（无序号），沙箱等 noCustomInput 场景不显示 */}
            {!noCustomInput && (
              <div
                className={`w-full text-left px-3 py-2.5 rounded-lg text-sm transition-colors cursor-pointer flex items-start gap-2.5 ${
                  isMultiSelect && selectedIndices.has(otherIdx)
                    ? 'glass-option-active'
                    : isOtherFocused
                      ? 'glass-option-active'
                      : 'text-content-disabled glass-option-hover border border-black/[0.06] border-dashed'
                }`}
                onClick={() => handleOptionClick(otherIdx, lang === 'zh-CN' ? '其他' : 'Other')}
              >
                {isMultiSelect ? (
                  <span className={`mt-0.5 w-4 h-4 rounded border flex items-center justify-center shrink-0 text-xs transition-colors ${
                    selectedIndices.has(otherIdx) ? 'bg-primary border-primary text-white' : 'border-border-light'
                  }`}>
                    {selectedIndices.has(otherIdx) ? '✓' : ''}
                  </span>
                ) : (
                  <span className={`mt-0.5 w-4 h-4 rounded-full border shrink-0 flex items-center justify-center transition-colors ${
                    isOtherFocused ? 'border-primary' : 'border-border-light'
                  }`}>
                    <span className={`w-2 h-2 rounded-full transition-colors ${isOtherFocused ? 'bg-primary' : 'bg-transparent'}`} />
                  </span>
                )}
                <div className="flex-1 min-w-0 flex items-center gap-1.5">
                  <span className={`text-sm font-medium shrink-0 ${
                    (isMultiSelect && selectedIndices.has(otherIdx)) || (!isMultiSelect && isOtherFocused) ? 'text-primary' : ''
                  }`}>
                    {lang === 'zh-CN' ? '其他' : 'Other'}
                  </span>{' '}
                  {isOtherFocused ? (
                    <input
                      ref={otherInputRef}
                      type="text"
                      value={otherText}
                      onChange={(e) => setOtherText(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') {
                          e.preventDefault();
                          handleOtherSubmit();
                        }
                        if (e.key === 'Escape') {
                          setIsOtherFocused(false);
                          setOtherText('');
                          if (isMultiSelect) {
                            setSelectedIndices((prev) => {
                              const next = new Set(prev);
                              next.delete(otherIdx);
                              return next;
                            });
                          }
                        }
                        e.stopPropagation();
                      }}
                      // 失焦自动提交：焦点离开输入框时提交已输入内容（无需回车）
                      // 多问题：不限制 relatedTarget 是否在卡片内——切问题 tab 也应提交"其他"内容，
                      // 否则用户输入的文字会丢失
                      // 单问题多选：焦点仍在卡片内（如点击普通选项或确认按钮）则不提交，
                      // 统一交给卡片失焦或确认按钮提交，避免点选其他选项时被误提交
                      onBlur={(e) => {
                        if (!isMultiQuestion) {
                          const next = e.relatedTarget as Node | null;
                          if (next && cardRef.current?.contains(next)) return;
                        }
                        // 单问题走守卫提交（卡片失焦会在同一事件中再触发一次，
                        // 守卫保证只提交一次）；多问题直接持久化
                        if (isMultiQuestion) {
                          handleOtherSubmit();
                        } else {
                          submitSingleSelect();
                        }
                      }}
                      onClick={(e) => e.stopPropagation()}
                      placeholder={lang === 'zh-CN' ? '输入后离开卡片自动提交（或按 Enter）' : 'Auto-submit on leaving card (or press Enter)'}
                      className="flex-1 min-w-0 bg-transparent border-none outline-none text-sm text-content-primary placeholder:text-content-disabled"
                      autoFocus
                    />
                  ) : (
                    <span className="text-sm text-content-disabled">
                      {otherText || (lang === 'zh-CN' ? '...' : '...')}
                    </span>
                  )}
                </div>
              </div>
            )}
          </div>
        ) : null}

        {/* 无选项时的输入框 */}
        {!hasOptions && (
          <div className="flex gap-2 items-end">
            <textarea
              value={otherText}
              onChange={(e) => setOtherText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault();
                  const text = otherText.trim();
                  if (!text) return;
                  if (isMultiQuestion) {
                    const header = currentQuestion?.header ?? `Q${currentIndex + 1}`;
                    recordAnswer(header, text);
                    setOtherText('');
                  } else {
                    // 与 submitSingleSelect 一致：单问题已提交（含跳过）后不再重复提交
                    if (submittedRef.current) return;
                    submittedRef.current = true;
                    onRespond(requestId, text);
                  }
                }
              }}
              placeholder={lang === 'zh-CN' ? '输入你的回答...' : 'Type your answer...'}
              rows={1}
              className="flex-1 resize-none bg-white/60 border border-white/40 rounded-lg px-3 py-2 text-sm outline-none focus:border-primary focus:shadow-glow transition-all duration-200"
            />
            <button
              onClick={() => {
                const text = otherText.trim();
                if (!text) return;
                if (isMultiQuestion) {
                  const header = currentQuestion?.header ?? `Q${currentIndex + 1}`;
                  recordAnswer(header, text);
                  setOtherText('');
                } else {
                  // 与 submitSingleSelect 一致：单问题已提交（含跳过）后不再重复提交
                  if (submittedRef.current) return;
                  submittedRef.current = true;
                  onRespond(requestId, text);
                }
              }}
              className="px-3 py-2 text-xs font-medium text-white bg-primary hover:bg-primary-hover rounded-md transition-colors cursor-pointer shrink-0"
            >
              {t(lang, 'send')}
            </button>
          </div>
        )}
      </div>

      <div className="px-4 py-3 border-t border-white/30 flex items-center justify-between">
        {/* 左侧：重置按钮（多问题时） */}
        <div>
          {isMultiQuestion && Object.keys(multiAnswers).length > 0 && (
            <button
              onClick={() => {
                setMultiAnswers({});
                setAllSelectedIndices({});
                setAllOtherText({});
                setSkippedHeaders(new Set());
                setCurrentIndex(0);
              }}
              className="px-3 py-1.5 text-xs font-medium text-content-secondary glass-option-hover rounded-md transition-colors cursor-pointer"
            >
              {lang === 'zh-CN' ? '重置' : 'Reset'}
            </button>
          )}
        </div>
        {/* 右侧：下一题 / 提交 / 确认 */}
        <div className="flex items-center gap-2">
          {isMultiQuestion && currentIndex < questions.length - 1 && (
            <button
              onClick={() => setCurrentIndex(currentIndex + 1)}
              className="px-3 py-1.5 text-xs font-medium text-white bg-primary hover:bg-primary-hover rounded-md transition-colors cursor-pointer"
            >
              {lang === 'zh-CN' ? '下一题' : 'Next'}
            </button>
          )}
          {/* 跳过按钮：跳过当前问题（多问题标记跳过并前进，单问题直接提交空答案） */}
          <button
            type="button"
            onClick={handleSkip}
            className="px-3 py-1.5 text-xs font-medium text-content-secondary glass-option-hover rounded-md transition-colors cursor-pointer"
          >
            {t(lang, 'question_skip')}
          </button>
          {isMultiQuestion && (Object.keys(multiAnswers).length + skippedHeaders.size) === questions.length && (
            <button
              onClick={() => {
                // 防重复提交：模态框需等后端 modal_request(modal=None) 回包才卸载，
                // 期间快速连点会发多条 question_response，触发后端 future 重复 resolve
                if (submittedRef.current) return;
                submittedRef.current = true;
                const result: Record<string, string | string[]> = {};
                for (const [k, v] of Object.entries(multiAnswers)) {
                  try {
                    const parsed = JSON.parse(v);
                    if (Array.isArray(parsed)) {
                      result[k] = parsed;
                    } else {
                      result[k] = v;
                    }
                  } catch {
                    result[k] = v;
                  }
                }
                onRespond(requestId, JSON.stringify(result));
              }}
              className="px-3 py-1.5 text-xs font-medium text-white bg-primary hover:bg-primary-hover rounded-md transition-colors cursor-pointer"
            >
              {lang === 'zh-CN' ? '提交' : 'Submit'}
            </button>
          )}
          {/* 单问题单选提交按钮：选中后点击提交（保留失焦自动提交兜底）；无选项纯输入场景走上方输入框 */}
          {!isMultiQuestion && !isMultiSelect && hasOptions && (
            <button
              type="button"
              onClick={() => submitSingleSelect()}
              disabled={!singleAnswer}
              className="px-3 py-1.5 text-xs font-medium text-white bg-primary hover:bg-primary-hover rounded-md transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
            >
              {t(lang, 'question_submit')}
            </button>
          )}
          {/* 单问题多选确认按钮：可见的提交入口，点击即提交全部选中项（保留失焦自动提交兜底） */}
          {!isMultiQuestion && isMultiSelect && (
            <button
              type="button"
              onClick={() => guardedSubmitSingleMultiSelect()}
              disabled={!canSubmitMulti}
              className="px-3 py-1.5 text-xs font-medium text-white bg-primary hover:bg-primary-hover rounded-md transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
            >
              {t(lang, 'multi_select_confirm')}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
