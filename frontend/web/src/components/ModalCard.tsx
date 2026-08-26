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

// ---- 权限原因本地化（后端英文硬编码，前端仅翻译会显示在权限弹窗上的 3 条 reason） ----

/** 英文 reason → i18n key（与 i18n.ts 的 perm_* 键对应；其余 reason 不进弹窗，保持原文） */
const PERMISSION_REASON_KEYS: Record<string, string> = {
  'High-risk operations require confirmation in auto mode': 'perm_auto_high_risk',
  'High-risk operations require confirmation in default mode': 'perm_default_high_risk',
  'Mutating tools require user confirmation in default mode': 'perm_default_mutating',
};

/** 权限原因本地化：中文界面下翻译展示，英文界面与未匹配的原文透传 */
function localizePermissionReason(reason: string, lang: UiLanguage): string {
  const key = PERMISSION_REASON_KEYS[reason];
  if (!key) return reason;
  return t(lang, key);
}

/** 是否为"其他"占位选项（LLM 返回的"其他"选项被过滤，只保留工具自动添加的） */
function isOtherLabel(label: string): boolean {
  const l = label.toLowerCase();
  return l === 'other' || l === '其他' || l.startsWith('other') || l.startsWith('其他');
}

// ---- 卡片底部按钮样式（对齐设置弹窗底部按钮规格：text-sm + px-4 py-2 + rounded-lg） ----

/** 主操作按钮（提交/下一题/确认）：主色底 */
const FOOTER_BTN_PRIMARY =
  'px-4 py-2 text-sm font-medium text-white bg-primary hover:bg-primary-hover rounded-lg transition-colors cursor-pointer focus:outline-none disabled:opacity-40 disabled:cursor-not-allowed';
/** 次要操作按钮（跳过/重置/弹窗打开）：带线框（medium 深一档保证可见性），悬浮浅色底 */
const FOOTER_BTN_SECONDARY =
  'px-4 py-2 text-sm font-medium text-content-secondary hover:bg-surface-hover rounded-lg transition-colors cursor-pointer focus:outline-none border border-border-medium';

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
  const reason = modal.reason ? localizePermissionReason(String(modal.reason), lang) : null;
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

  // 键盘导航：上下箭头切换选中项（初始选中第一项），回车执行当前选中项。
  // 使用 window 级监听而非卡片 onFocus——DOM 焦点会随点击主题切换、滚动等
  // 操作离开卡片，导致箭头失效。
  // 按键作用域拆分：箭头键对按钮等控件无原生语义，全局处理；
  // Enter 会激活聚焦的控件（click），焦点在卡片外的可激活控件上时必须放行
  // 原生行为，否则会在其他弹窗/按钮上隐式触发权限操作。
  const cardRef = useRef<HTMLDivElement>(null);
  const [activeIdx, setActiveIdx] = useState(0);

  const handleKeyDown = (e: KeyboardEvent) => {
    const target = e.target as HTMLElement | null;
    if (target?.tagName === 'INPUT' || target?.tagName === 'TEXTAREA' || target?.tagName === 'SELECT' || target?.isContentEditable) return;
    if (target?.closest('[data-card-footer]')) return;
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      const delta = e.key === 'ArrowDown' ? 1 : -1;
      setActiveIdx((i) => Math.max(0, Math.min(permissionOptions.length - 1, i + delta)));
      return;
    }
    // Enter 仅在焦点位于卡片内或 body 时接管；卡片外可激活控件交还原生 click
    const insideCard = !!target && !!cardRef.current?.contains(target);
    const onActivatable = target?.tagName === 'BUTTON' || target?.tagName === 'A'
      || target?.tagName === 'SUMMARY' || target?.getAttribute('role') === 'button';
    if (e.key === 'Enter' && (insideCard || (!onActivatable && (target === document.body || target === document.documentElement)))) {
      e.preventDefault();
      permissionOptions[activeIdx]?.onClick();
    }
  };
  // ref 持有最新 handler，监听只注册一次
  const keyHandlerRef = useRef(handleKeyDown);
  keyHandlerRef.current = handleKeyDown;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => keyHandlerRef.current(e);
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  return (
    <div ref={cardRef} className="my-3 rounded-2xl glass-surface overflow-hidden">
      {/* 标题区：警告图标 + 权限确认内容 */}
      <div className="px-4 py-3">
        <div className="flex items-center gap-2 mb-1">
          <svg className="w-4 h-4 text-amber-500 shrink-0" viewBox="0 0 16 16" fill="currentColor">
            <path d="M8.982 1.566a1.13 1.13 0 0 0-1.96 0L.165 13.233c-.457.778.091 1.767.98 1.767h13.713c.889 0 1.438-.99.98-1.767L8.982 1.566zM8 5c.535 0 .954.462.9.995l-.35 3.507a.552.552 0 0 1-1.1 0L7.1 5.995A.905.905 0 0 1 8 5zm.002 6a1 1 0 1 1 0 2 1 1 0 0 1 0-2z" />
          </svg>
          <span className="text-sm font-medium text-content-primary">
            {t(lang, 'allow_tool')}{' '}
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
        {permissionOptions.map((opt, idx) => (
          <button
            key={opt.key}
            onClick={() => { setActiveIdx(idx); opt.onClick(); }}
            onMouseDown={(e) => e.preventDefault()}
            className={`w-full text-left px-3 py-2.5 rounded-lg text-sm transition-colors cursor-pointer flex items-center gap-2.5 focus:outline-none ${
              idx === activeIdx
                ? 'glass-option-active glass-option-hover'
                : 'glass-option-hover border border-transparent'
            }`}
          >
            <span className="mt-0.5 w-4 h-4 rounded-full border shrink-0 flex items-center justify-center">
              <span className={`w-2 h-2 rounded-full ${idx === activeIdx ? 'bg-primary' : 'bg-transparent'}`} />
            </span>
            <div className="flex-1 min-w-0">
              <div className={`text-sm font-medium ${idx === activeIdx ? 'text-primary' : 'text-content-primary'}`}>
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
  /** 多问题切换题目时回调（ChatArea 传入其"回到底部"滚动逻辑，复用现有滚动而非自定义） */
  onTabChange?: () => void;
}

/**
 * 问答卡片组件
 *
 * 显示问题并支持单选、多选或自定义输入回答。
 *
 * @param props - 组件属性
 * @returns 返回问答卡片的 JSX 元素
 */
export function QuestionCard({ modal, lang, onRespond, onTabChange }: QuestionCardProps) {
  const requestId = String(modal.request_id ?? '');
  const questions: QuestionItem[] = Array.isArray(modal.questions) ? (modal.questions as QuestionItem[]) : [];
  // ---- 多问题状态 ----
  const [currentIndex, setCurrentIndex] = useState(0);
  const [multiAnswers, setMultiAnswers] = useState<Record<string, string>>({});
  const isMultiQuestion = questions.length > 1;
  const currentQuestion = questions.length > 0 ? (questions[currentIndex] ?? questions[0]!) : null;
  const options = currentQuestion?.options ?? [];
  // 过滤掉LLM返回的"其他"选项，保留工具自动添加的
  const filteredOptions = useMemo(() => options.filter((opt) => !isOtherLabel(opt.label)), [options]);
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
  /**
   * 键盘导航光标（0 起始，含"其他"行）。
   * 单问题单选场景下光标即选中项（箭头直接选中）；多选与沙箱单选场景下
   * 光标与选中态分离（空格/点击切换勾选，回车提交），光标以 .kbd-cursor 描边显示。
   */
  const [activeIdx, setActiveIdx] = useState(0);
  const otherInputRef = useRef<HTMLInputElement>(null);
  /** 问题卡片根元素引用，用于挂载时自动聚焦与"其他"输入框失焦的卡片内判定 */
  const cardRef = useRef<HTMLDivElement>(null);
  /**
   * 最近一次指针按下是否发生在卡片内：点击标题/留白等不可聚焦区域时焦点落到
   * body，"其他"输入框 blur 的 relatedTarget 为 null 无法区分内外，
   * 以此标记放行（仅服务于输入框的 onBlur 判定）
   */
  const pointerInsideRef = useRef(false);
  /**
   * 已提交守卫：防止同一问题重复发出 question_response（后端 future 已 resolve
   * 后再次 set_result 抛 InvalidStateError）。典型冲突场景："其他"输入框 blur
   * 提交与回车/按钮点击在同一事件序列中各触发一次。request_id 变化时重置。
   */
  const submittedRef = useRef(false);
  /** 计划内容弹窗查看状态（与右栏文件预览的弹窗打开一致） */
  const [planPopOut, setPlanPopOut] = useState(false);

  // 计划弹窗 Esc 关闭（对齐 FileViewerModal 交互）
  useEffect(() => {
    if (!planPopOut) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setPlanPopOut(false);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [planPopOut]);

  // 切换问题时恢复"其他"输入框的聚焦/显示状态：
  // 若该题已有"其他"输入内容（allOtherText 持久化），则显示输入框与选中态，
  // 否则收起。这样回到已填"其他"的问题时，界面能正确回显勾选与文本。
  useEffect(() => {
    const persistedOther = allOtherText[currentIndex]?.trim() ?? '';
    setIsOtherFocused(persistedOther.length > 0);
  }, [currentIndex, allOtherText]);

  /**
   * 切换题目：同步重置键盘光标（不使用 effect 异步重置——paint 后执行的
   * setActiveIdx 会与用户切 tab 后的第一次按键竞争，导致移动被覆盖）。
   * 目标题已有"其他"文本时光标停在"其他"行：直接回车会重新展开编辑，
   * 避免误选中普通选项而清空已输入的"其他"内容。
   */
  const goToQuestion = useCallback((idx: number) => {
    setCurrentIndex(idx);
    const target = questions[idx];
    if (!target) {
      setActiveIdx(0);
      return;
    }
    const hasOtherRow = target.noCustomInput !== true;
    const targetOpts = (target.options ?? []).filter((o) => !isOtherLabel(o.label));
    setActiveIdx(hasOtherRow && (allOtherText[idx]?.trim() ?? '') ? targetOpts.length : 0);
  }, [questions, allOtherText]);

  // 多问题切换题目后：题目内容长短不同导致卡片高度变化、位置可能跳动。
  // 复用 ChatArea 现有的"回到底部"滚动逻辑（smooth 滚到容器底部），不自定义滚动
  useEffect(() => {
    if (!isMultiQuestion) return;
    onTabChange?.();
  }, [currentIndex, isMultiQuestion, onTabChange]);

  // 单选已选答案（从 multiAnswers 回读）
  const currentHeader = currentQuestion?.header ?? `Q${currentIndex + 1}`;
  /**
   * "其他"输入是否已接管答案：仅当输入框存在合法值（非空文本）时成立。
   * 仅聚焦不算——用户点开"其他"查看后反悔，不应丢失已选的普通选项。
   */
  const otherEngaged = otherText.trim().length > 0;
  /** "其他"文本接管单选答案（仅单问题单选场景） */
  const otherTakesOver = !isMultiSelect && !isMultiQuestion && otherEngaged;
  /**
   * 单问题单选：当前答案。普通选项优先（`序号. 标签` 格式），否则"其他"文本。
   * 光标初始停在第一项，因此未操作时答案默认为第一项——回车即可直接提交。
   */
  const singleAnswer = isMultiSelect || isMultiQuestion
    ? null
    : otherTakesOver
      ? otherText.trim()
      : filteredOptions[activeIdx]
        ? `${activeIdx + 1}. ${filteredOptions[activeIdx]!.label}`
        : null;

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

  // 单问题多选确认提交：把当前全部选中项提交给后端（提交按钮 / 回车 / "其他"失焦触发）。
  // 空选时不提交也不置守卫——否则先失焦一次后守卫卡死，后续确认按钮会失效。
  const confirmSingleMultiSelect = useCallback(() => {
    if (!isMultiSelect || isMultiQuestion || submittedRef.current) return;
    const labels = filteredOptions
      .filter((_, i) => selectedIndices.has(i))
      .map((o) => o.label);
    if (selectedIndices.has(otherIdx) && otherText.trim()) {
      labels.push(otherText.trim());
    }
    if (labels.length === 0) return;
    submittedRef.current = true;
    onRespond(requestId, JSON.stringify({ [currentHeader]: labels }));
  }, [isMultiSelect, isMultiQuestion, filteredOptions, selectedIndices, otherIdx, otherText, requestId, onRespond, currentHeader]);

  // 单问题单选提交：由提交按钮、回车或"其他"输入框失焦触发。
  // overrideAnswer 供键盘路径使用——回车时 state 尚未更新，直接显式传入答案
  const submitSingleSelect = useCallback((overrideAnswer?: string) => {
    if (isMultiSelect || isMultiQuestion) return;
    const fallback = singleAnswer;
    const answer = overrideAnswer ?? fallback;
    if (!answer || submittedRef.current) return;
    submittedRef.current = true;
    onRespond(requestId, answer);
  }, [isMultiSelect, isMultiQuestion, singleAnswer, requestId, onRespond]);

  // 多问题统一提交：把各题答案（JSON 数组还原为列表）合并为一个 JSON 对象提交。
  // skipped 中的题目不写入结果。底部提交按钮与 Ctrl+Enter 快捷键共用；
  // 鼠标点击不转移焦点（onMouseDown preventDefault），"其他"输入框不会 blur
  // 落盘——单选题在此补写其未落盘的合法值，避免直接提交丢失。
  // 未作答也未跳过的单选题以默认选中项（第一项）作答，与"下一题"的
  // 落盘语义一致——直接提交不丢最后一题。
  const submitMultiNow = useCallback(() => {
    if (submittedRef.current) return;
    submittedRef.current = true;
    const answers = { ...multiAnswers };
    const skippedEff = new Set(skippedHeaders);
    const pending = allOtherText[currentIndex]?.trim();
    if (pending && !isMultiSelect) {
      // 单选：有合法值即视为作答，写入答案并撤销该题的跳过标记
      answers[currentHeader] = pending;
      skippedEff.delete(currentHeader);
    } else if (pending && isMultiSelect && selectedIndices.has(otherIdx)) {
      // 多选：鼠标点击不转移焦点（preventDefault），输入框不会 blur 落盘，
      // 把未落盘的"其他"文本并入该题已记录的多选答案数组，避免静默丢失
      let arr: string[] = [];
      try {
        const parsed = JSON.parse(answers[currentHeader] ?? '[]');
        if (Array.isArray(parsed)) arr = parsed.map(String);
      } catch { /* 旧格式异常时从空数组重建 */ }
      if (!arr.includes(pending)) arr.push(pending);
      answers[currentHeader] = JSON.stringify(arr);
    } else if (!skippedEff.has(currentHeader) && !(currentHeader in answers)
        && !isMultiSelect && hasOptions && filteredOptions.length > 0) {
      answers[currentHeader] = `1. ${filteredOptions[0]!.label}`;
    }
    const result: Record<string, string | string[]> = {};
    for (const [k, v] of Object.entries(answers)) {
      if (skippedEff.has(k)) continue;
      try {
        const parsed = JSON.parse(v);
        result[k] = Array.isArray(parsed) ? parsed : v;
      } catch {
        result[k] = v;
      }
    }
    onRespond(requestId, JSON.stringify(result));
  }, [multiAnswers, allOtherText, currentIndex, isMultiSelect, selectedIndices, otherIdx, currentHeader, skippedHeaders, requestId, onRespond]);

  // 新模态框（request_id 变化）到来时重置提交守卫
  useEffect(() => {
    submittedRef.current = false;
  }, [requestId]);

  /**
   * 前进到下一题并落盘当前题答案。
   * 只要用户没有显式点击"跳过"，当前题的默认选中选项（第一项，已有逻辑）
   * 就视为用户的选项——点击"下一题"/按 → 即接受该默认值；已有"其他"
   * 待落盘文本时优先记录"其他"。多选无默认选中概念，空选不落盘。
   */
  const advanceWithDefault = useCallback(() => {
    const header = currentQuestion?.header ?? `Q${currentIndex + 1}`;
    const pending = allOtherText[currentIndex]?.trim();
    if (pending && !isMultiSelect) {
      recordAnswer(header, pending);
    } else if (!isMultiSelect && !skippedHeaders.has(header)
        && !(header in multiAnswers) && hasOptions && filteredOptions.length > 0) {
      recordAnswer(header, `1. ${filteredOptions[0]!.label}`);
    }
    if (currentIndex < questions.length - 1) {
      goToQuestion(currentIndex + 1);
    }
  }, [currentQuestion, currentIndex, allOtherText, isMultiSelect, skippedHeaders, multiAnswers, hasOptions, filteredOptions, questions.length, recordAnswer, goToQuestion]);

  const handleOptionClick = useCallback(
    (idx: number, label: string) => {
      if (isMultiSelect) {
        // 鼠标点击同步键盘光标位置
        setActiveIdx(idx);
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
      // 单选"其他"选项：展开输入框并聚焦。不清除已记录的普通选项答案——
      // 仅当"其他"输入了合法值（otherEngaged）后才在视觉与提交层面接管该题
      if (idx === otherIdx) {
        setActiveIdx(idx);
        setIsOtherFocused(true);
        setTimeout(() => otherInputRef.current?.focus(), 0);
        return;
      }
      // 单选
      if (isMultiQuestion) {
        setActiveIdx(idx);
        // 选择普通选项即放弃该题"其他"文本与输入态（与单问题单选语义一致）。
        // 否则 otherEngaged 恒真会短路选中态渲染，导致箭头切换时视觉上无反应
        setIsOtherFocused(false);
        setOtherText('');
        recordAnswer(currentHeader, `${idx + 1}. ${label}`);
      } else {
        // 单问题单选：光标即选中项（箭头/点击共用），并收起"其他"输入框。
        // 沙箱类（noCustomInput）场景选中即自动提交，无底部提交按钮；
        // 普通问题保留"选中 + 提交按钮/回车"路径
        setActiveIdx(idx);
        setIsOtherFocused(false);
        setOtherText('');
        if (currentQuestion?.noCustomInput) {
          submittedRef.current = true;
          onRespond(requestId, `${idx + 1}. ${label}`);
        }
      }
    },
    [isMultiSelect, otherIdx, isMultiQuestion, currentHeader, commitMultiSelect, recordAnswer, currentQuestion?.noCustomInput, requestId, onRespond],
  );

  // 多选"其他"输入回车时提交：
  // - 单问题多选：把"其他"勾选并触发确认提交
  // - 多问题多选：写入 multiAnswers（统一格式）
  const handleMultiConfirm = useCallback(() => {
    if (!isMultiQuestion) {
      confirmSingleMultiSelect();
    } else {
      commitMultiSelect(selectedIndices);
    }
  }, [isMultiQuestion, selectedIndices, commitMultiSelect, confirmSingleMultiSelect]);

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
        goToQuestion(currentIndex + 1);
      }
    } else {
      if (!submittedRef.current) {
        submittedRef.current = true;
        onRespond(requestId, '');
      }
    }
  }, [isMultiQuestion, skippedHeaders, currentHeader, currentIndex, questions.length, submittedRef, requestId, onRespond, goToQuestion]);

  // ---- 键盘导航 ----
  /** 可导航条目数：普通选项 + "其他"行（沙箱等 noCustomInput 场景无"其他"） */
  const itemCount = hasOptions ? filteredOptions.length + (noCustomInput ? 0 : 1) : 0;
  /** 光标与选中态分离的场景（需要 .kbd-cursor 描边指示光标位置） */
  const cursorVisible = hasOptions && (isMultiSelect || noCustomInput === true);

  /**
   * 多问题完成数 = 已记录答案 + 当前题"其他"合法值待落盘 + 已跳过。
   * "其他"待落盘单独计入，避免用户输入过程中找不到提交按钮。
   */
  const multiDoneCount = isMultiQuestion
    ? Object.keys(multiAnswers).length
      + (!isMultiSelect && otherEngaged
          && !(currentHeader in multiAnswers) && !skippedHeaders.has(currentHeader)
        ? 1 : 0)
      + skippedHeaders.size
    : 0;
  /** 是否可提交：全部题目完成；或在最后一题仅剩当前题未完成（提交时以默认选中项补写） */
  const canSubmitMultiNow = isMultiQuestion
    && (multiDoneCount === questions.length
      || (currentIndex === questions.length - 1 && multiDoneCount >= questions.length - 1));

  // 键盘导航使用 window 级监听而非卡片 onFocus——DOM 焦点会随点击主题切换、
  // 滚动页面等操作离开卡片，导致箭头/回车失效。全局监听排除：
  // - 可编辑控件（input/textarea/select/contenteditable）：打字场景交还原生行为
  // - 底部按钮栏 [data-card-footer]：按钮自带 onClick
  // - 计划弹窗打开期间：弹窗内阅读/滚动不受干扰
  //
  // 快捷键约定：
  // - ↑/↓ 在选项间移动（单选箭头即选中）
  // - ←/→ 仅多问题模式生效，切换题目 tab
  // - 单问题单选：Enter 提交；多选/多问题：Enter 仅勾选/选中，
  //   Ctrl/Cmd+Enter 统一为提交（多问题需全部题目完成）
  const handleCardKeyDown = (e: KeyboardEvent) => {
    if (planPopOut) return;
    const target = e.target as HTMLElement | null;
    const tag = target?.tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || target?.isContentEditable) return;
    if (target?.closest('[data-card-footer]')) return;
    // 按键作用域拆分（与 PermissionCard 一致）：箭头键对按钮等控件无原生语义，
    // 全局处理（保证点击主题按钮等场景后仍可用）；Enter/Space 会激活聚焦的控件，
    // 焦点在卡片外的可激活控件上时放行原生行为，避免隐式触发卡片提交
    const insideCard = !!target && !!cardRef.current?.contains(target);
    const onActivatable = tag === 'BUTTON' || tag === 'A'
      || tag === 'SUMMARY' || target?.getAttribute('role') === 'button';
    const confirmScopeOk = insideCard || (!onActivatable && (target === document.body || target === document.documentElement));

    // 多问题模式：←/→ 切换题目 tab（→ 前进时落盘当前题默认答案，与"下一题"一致）
    if (isMultiQuestion && (e.key === 'ArrowLeft' || e.key === 'ArrowRight')) {
      e.preventDefault();
      if (e.key === 'ArrowRight') {
        advanceWithDefault();
        return;
      }
      const next = Math.max(0, currentIndex - 1);
      goToQuestion(next);
      return;
    }

    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      if (itemCount === 0) return;
      e.preventDefault();
      const delta = e.key === 'ArrowDown' ? 1 : -1;
      const next = Math.max(0, Math.min(itemCount - 1, activeIdx + delta));
      if (next === activeIdx) return;
      setActiveIdx(next);
      // 单问题单选：箭头即选中（多问题单选也同步记录，与点击语义一致）；
      // 光标移到"其他"行时仅移动描边，回车才展开输入框，保证浏览顺畅
      if (!isMultiSelect && !noCustomInput && next < filteredOptions.length) {
        handleOptionClick(next, filteredOptions[next]!.label);
      }
      return;
    }

    // 多选：空格切换光标所在项的勾选
    if (e.key === ' ') {
      if (!isMultiSelect) return;
      if (!confirmScopeOk) return;
      e.preventDefault();
      if (activeIdx < filteredOptions.length) {
        handleOptionClick(activeIdx, filteredOptions[activeIdx]!.label);
      } else {
        handleOptionClick(otherIdx, lang === 'zh-CN' ? '其他' : 'Other');
      }
      return;
    }

    if (e.key !== 'Enter') return;
    if (!confirmScopeOk) return;
    e.preventDefault();
    if (!hasOptions) return;

    // Ctrl/Cmd+Enter：统一提交快捷键
    if (e.ctrlKey || e.metaKey) {
      if (isMultiQuestion) {
        // 多问题：全部题目完成（或最后一题仅剩当前题，提交时补默认值）才提交
        if (canSubmitMultiNow) submitMultiNow();
      } else if (isMultiSelect) {
        confirmSingleMultiSelect();
      }
      return;
    }

    // 光标停在"其他"行：已勾选且多选 → 重新展开输入框继续编辑；
    // 否则按点击语义展开输入框（多选未勾选时展开并勾选）
    if (activeIdx >= filteredOptions.length) {
      if (isMultiSelect && selectedIndices.has(otherIdx)) {
        setIsOtherFocused(true);
        setTimeout(() => otherInputRef.current?.focus(), 0);
        return;
      }
      handleOptionClick(otherIdx, lang === 'zh-CN' ? '其他' : 'Other');
      return;
    }
    const opt = filteredOptions[activeIdx];
    if (!opt) return;

    if (isMultiQuestion) {
      // 多问题模式：Enter 仅"选中"当前项（单选记录答案 / 多选切换勾选），
      // 不推进不提交——切题用 ←/→，最终提交走底部按钮或 Ctrl+Enter
      handleOptionClick(activeIdx, opt.label);
      return;
    }

    if (isMultiSelect) {
      // 单问题多选：Enter 仅勾选当前项，Ctrl+Enter 才提交
      handleOptionClick(activeIdx, opt.label);
      return;
    }

    // 单问题单选：Enter 提交当前选中项
    const answer = `${activeIdx + 1}. ${opt.label}`;
    // 沙箱类（noCustomInput）：光标与选中分离，回车直接提交高亮项
    if (currentQuestion?.noCustomInput) {
      submittedRef.current = true;
      onRespond(requestId, answer);
      return;
    }
    handleOptionClick(activeIdx, opt.label);
    submitSingleSelect(answer);
  };
  // ref 持有最新 handler，window 监听只注册一次（挂载时）
  const keyHandlerRef = useRef(handleCardKeyDown);
  keyHandlerRef.current = handleCardKeyDown;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => keyHandlerRef.current(e);
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  const questionText = currentQuestion?.question ?? String(modal.question ?? 'Question');
  const hintText = isMultiSelect
    ? (lang === 'zh-CN' ? '选择所有适用项' : 'Select all that apply')
    : (lang === 'zh-CN' ? '选择一项' : 'Select one');
  /** 单问题多选的提交快捷键小字（Enter 仅勾选，Ctrl+Enter 提交） */
  const submitHint = !isMultiQuestion && isMultiSelect
    ? (lang === 'zh-CN' ? ' · Ctrl+Enter 提交' : ' · Ctrl+Enter to submit')
    : '';
  /** 计划内容（类型安全提取；弹窗内渲染时无外层 typeof 守卫） */
  const planText = typeof modal.plan === 'string' ? modal.plan : '';

  /**
   * "其他"行是否处于选中高亮态：
   * - 多选：勾选了"其他"复选框
   * - 单选：输入框聚焦，或当前题答案来自"其他"文本（落盘后仍保持高亮）
   */
  const otherActive = isMultiSelect
    ? selectedIndices.has(otherIdx)
    : isOtherFocused
      || (isMultiQuestion
        ? !!multiAnswers[currentHeader] && !filteredOptions.some(
            (_, i) => multiAnswers[currentHeader] === `${i + 1}. ${filteredOptions[i]!.label}`)
        : otherTakesOver);

  return (
    <>
    <div
      ref={cardRef}
      onPointerDownCapture={() => {
        // 记录最近一次指针按下发生在卡片内，供"其他"输入框 onBlur 判定使用
        pointerInsideRef.current = true;
      }}
      className="my-3 rounded-2xl glass-surface overflow-hidden"
    >
      {typeof modal.plan === 'string' && modal.plan && (
        <div className="px-4 pt-3">
          <div className="border border-info/40 rounded-lg px-3 py-2.5 bg-info/5 max-h-80 overflow-y-auto">
            <div className="text-info font-semibold text-sm mb-2 flex items-center gap-1.5">
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
                  onClick={() => goToQuestion(idx)}
                  className={`px-2 py-0.5 rounded text-xs font-medium transition-colors cursor-pointer inline-flex items-center gap-1 whitespace-nowrap ${
                    isActive
                      ? 'bg-primary text-white'
                      : isAnswered
                        ? 'bg-primary-light text-primary border border-primary/20'
                        : 'glass-option-hover text-content-secondary'
                  }`}
                >
                  {isAnswered && !isActive && (
                    <svg width="8" height="8" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" className="shrink-0">
                      <path d="M4 8.5l2.5 2.5L12 5" />
                    </svg>
                  )}
                  {headerLabel}
                </button>
              );
            })}
          </div>
        )}
        <div className="text-sm font-medium text-content-primary">{questionText}</div>
        {hasOptions && (
          <div className="text-xs text-content-disabled mt-0.5">{hintText}{submitHint}</div>
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
              // 多选：从持久化状态读取；单选：
              // - "其他"输入存在合法值（otherEngaged）时答案由"其他"接管，抑制所有普通选项选中态
              // - 多问题：否则已作答以记录的答案为准；未作答时以键盘光标位为"预选中"
              //   （初始光标在第一项 → 第一项高亮，回车即提交）
              // - 单问题：以键盘光标为选中项
              const recorded = isMultiQuestion ? (multiAnswers[currentHeader] ?? null) : undefined;
              const isSelected = isMultiSelect
                ? selectedIndices.has(i)
                : otherEngaged
                  ? false
                  : isMultiQuestion
                    ? recorded === `${i + 1}. ${opt.label}` || (!recorded && activeIdx === i)
                    : activeIdx === i;
              return (
                <button
                  key={i}
                  onClick={() => handleOptionClick(i, opt.label)}
                  onMouseDown={(e) => e.preventDefault()}
                  className={`w-full text-left px-3 py-2.5 rounded-lg text-sm transition-colors cursor-pointer flex items-start gap-2.5 focus:outline-none ${
                    cursorVisible && activeIdx === i ? 'kbd-cursor' : ''
                  } ${
                    isSelected
                      ? 'glass-option-active glass-option-hover'
                      : 'border border-transparent glass-option-hover'
                  }`}
                >
                  {isMultiSelect ? (
                    <span className={`mt-0.5 w-4 h-4 rounded border flex items-center justify-center shrink-0 text-xs transition-colors ${
                      isSelected ? 'bg-primary border-primary text-white' : 'border-border-light'
                    }`}>
                      {isSelected ? <svg width="10" height="10" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M4 8.5l2.5 2.5L12 5" /></svg> : ''}
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
            {/* "其他"选项：内联输入框，与普通选项格式一致（无序号），沙箱等 noCustomInput 场景不显示。
                边框与普通未选中选项一致（透明），避免浅色主题下虚线框可见；
                键盘光标停在本行时以 kbd-cursor 描边指示（所有模式） */}
            {!noCustomInput && (
              <div
                className={`w-full text-left px-3 py-2.5 rounded-lg text-sm transition-colors cursor-pointer flex items-start gap-2.5 border border-transparent ${
                  activeIdx === otherIdx ? 'kbd-cursor' : ''
                } ${
                  otherActive
                    ? 'glass-option-active glass-option-hover'
                    : 'text-content-disabled glass-option-hover'
                }`}
                onClick={() => handleOptionClick(otherIdx, lang === 'zh-CN' ? '其他' : 'Other')}
              >
                {isMultiSelect ? (
                  <span className={`mt-0.5 w-4 h-4 rounded border flex items-center justify-center shrink-0 text-xs transition-colors ${
                    otherActive ? 'bg-primary border-primary text-white' : 'border-border-light'
                  }`}>
                    {otherActive ? <svg width="10" height="10" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M4 8.5l2.5 2.5L12 5" /></svg> : ''}
                  </span>
                ) : (
                  <span className={`mt-0.5 w-4 h-4 rounded-full border shrink-0 flex items-center justify-center transition-colors ${
                    otherActive ? 'border-primary' : 'border-border-light'
                  }`}>
                    <span className={`w-2 h-2 rounded-full transition-colors ${otherActive ? 'bg-primary' : 'bg-transparent'}`} />
                  </span>
                )}
                <div className="flex-1 min-w-0 flex items-center gap-1.5">
                  <span className={`text-sm font-medium shrink-0 ${
                    otherActive ? 'text-primary' : ''
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
                        // IME 输入法组合期间（拼音候选确认）不响应 Enter/Escape，
                        // 避免把半成品文本当作答案落盘或误收起输入框
                        if (e.nativeEvent.isComposing) return;
                        if (e.key === 'Enter') {
                          e.preventDefault();
                          const text = otherText.trim();
                          if (isMultiSelect) {
                            // 多选模式：Enter 仅退出输入状态（收起输入框、锁定文本与勾选），
                            // 不提交——提交统一走 Ctrl+Enter / 确认按钮。
                            // 多问题多选：先把文本落盘（勾选已即时写入），避免收起后丢失
                            if (isMultiQuestion && text) {
                              commitMultiSelect(selectedIndices);
                            }
                            setIsOtherFocused(false);
                          } else if (isMultiQuestion) {
                            // 多问题单选：Enter 落盘当前题答案并收起输入框，
                            // 之后可用 ↑/↓ 切换选项、←/→ 切换题目
                            if (text) recordAnswer(currentHeader, text);
                            setIsOtherFocused(false);
                          } else {
                            // 单问题单选：Enter 提交"其他"文本（唯一提交语义）
                            handleOtherSubmit();
                          }
                        }
                        if (e.key === 'Escape') {
                          setIsOtherFocused(false);
                          setOtherText('');
                          // 光标收回普通选项区首项，保证收起后回车提交路径可用
                          setActiveIdx(0);
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
                      // 失焦自动提交（全卡片唯一保留的失焦提交路径，仅限"其他"输入框）。
                      // 注意：App 全局禁止了按钮 mousedown 聚焦后，点击 tab/选项等
                      // 按钮不再触发本输入框 blur——草稿由切题恢复与 submitMultiNow
                      // 回填保护，不会丢失；blur 提交实际仅在焦点移向非按钮元素时发生：
                      // 多问题：不限制 relatedTarget 是否在卡片内（键盘 Tab 切题仍提交）
                      // 单问题：焦点仍在卡片内则不提交，由回车/确认按钮统一提交
                      onBlur={(e) => {
                        if (!isMultiQuestion) {
                          const next = e.relatedTarget as Node | null;
                          if (next && cardRef.current?.contains(next)) return;
                          // 点击卡片内不可聚焦区域（标题/留白/plan 文本）时焦点落到 body
                          // （relatedTarget=null），并非真正离开卡片——不提交，避免误提交
                          // 整卡答案并置位守卫导致后续无法修改
                          if (!next && pointerInsideRef.current) {
                            pointerInsideRef.current = false;
                            return;
                          }
                        }
                        if (isMultiQuestion) {
                          handleOtherSubmit();
                        } else {
                          submitSingleSelect();
                        }
                      }}
                      onClick={(e) => e.stopPropagation()}
                      placeholder={
                        !isMultiSelect && !isMultiQuestion
                          ? (lang === 'zh-CN' ? '输入后按 Enter 提交' : 'Press Enter to submit')
                          : isMultiSelect
                            ? (lang === 'zh-CN' ? '按 Enter 收起（Ctrl+Enter 提交）' : 'Enter to collapse (Ctrl+Enter to submit)')
                            : (lang === 'zh-CN' ? '按 Enter 收起（←/→ 切换题目）' : 'Enter to collapse (←/→ switch questions)')
                      }
                      // 不设 autoFocus：切 tab 恢复"其他"输入状态时只展开输入框、不抢焦点，
                      // 箭头/回车等键盘操作保持可用；聚焦仅由显式操作（点击"其他"行或 Enter 展开）触发
                      className="flex-1 min-w-0 bg-transparent border-none outline-none text-sm text-content-primary placeholder:text-content-disabled"
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
              className={FOOTER_BTN_PRIMARY + ' shrink-0'}
            >
              {t(lang, 'send')}
            </button>
          </div>
        )}
      </div>

      {/* 底部按钮栏：无分割线，按钮规格对齐设置弹窗（text-sm + px-4 py-2 + rounded-lg），
          次要按钮带 border-border-medium 细线框；onMouseDown preventDefault 保持卡片持有焦点，
          键盘导航在鼠标点击按钮后依然可用 */}
      <div data-card-footer className="px-4 py-3 flex items-center justify-between">
        {/* 左侧：计划弹窗打开（有 plan 时）+ 重置按钮（多问题时） */}
        <div className="flex items-center gap-1.5">
          {typeof modal.plan === 'string' && modal.plan && (
            <button
              onClick={() => setPlanPopOut(true)}
              onMouseDown={(e) => e.preventDefault()}
              title={t(lang, 'popout_preview')}
              aria-label={t(lang, 'popout_preview')}
              className={FOOTER_BTN_SECONDARY + ' shrink-0'}
            >
              {t(lang, 'popout_preview')}
            </button>
          )}
          {isMultiQuestion && Object.keys(multiAnswers).length > 0 && (
            <button
              onClick={() => {
                setMultiAnswers({});
                setAllSelectedIndices({});
                setAllOtherText({});
                setSkippedHeaders(new Set());
                // 不走 goToQuestion——其读取的 allOtherText 闭包尚未更新（已清空），
                // 会让光标误停在刚被清空的"其他"行；重置后光标固定回第一项
                setCurrentIndex(0);
                setActiveIdx(0);
              }}
              onMouseDown={(e) => e.preventDefault()}
              className={FOOTER_BTN_SECONDARY}
            >
              {lang === 'zh-CN' ? '重置' : 'Reset'}
            </button>
          )}
        </div>
        {/* 右侧：下一题 / 提交 / 确认 / 跳过 */}
        <div className="flex items-center gap-2">
          {isMultiQuestion && currentIndex < questions.length - 1 && (
            <button
              onClick={advanceWithDefault}
              onMouseDown={(e) => e.preventDefault()}
              className={FOOTER_BTN_PRIMARY}
            >
              {lang === 'zh-CN' ? '下一题' : 'Next'}
            </button>
          )}
          {/* 跳过按钮：跳过当前问题（多问题标记跳过并前进，单问题直接提交空答案）。
              沙箱类（noCustomInput）场景已改为选中即提交，跳过按钮冗余 */}
          {!currentQuestion?.noCustomInput && (
            <button
              type="button"
              onClick={handleSkip}
              onMouseDown={(e) => e.preventDefault()}
              className={FOOTER_BTN_SECONDARY}
            >
              {t(lang, 'question_skip')}
            </button>
          )}
          {/* 完成数见 multiDoneCount；最后一题放宽一题余量：submitMultiNow
              会以默认选中项（第一项）补写未作答未跳过的单选题 */}
          {canSubmitMultiNow && (
            <button
              onClick={submitMultiNow}
              onMouseDown={(e) => e.preventDefault()}
              className={FOOTER_BTN_PRIMARY}
            >
              {lang === 'zh-CN' ? '提交' : 'Submit'}
            </button>
          )}
          {/* 单问题单选提交按钮：选中后点击或回车提交；无选项纯输入场景走上方输入框。
              沙箱类（noCustomInput）场景已改为选中即提交，无需提交按钮 */}
          {!isMultiQuestion && !isMultiSelect && hasOptions && !currentQuestion?.noCustomInput && (
            <button
              type="button"
              onClick={() => submitSingleSelect()}
              disabled={!singleAnswer}
              onMouseDown={(e) => e.preventDefault()}
              className={FOOTER_BTN_PRIMARY}
            >
              {t(lang, 'question_submit')}
            </button>
          )}
          {/* 单问题多选确认按钮：可见的提交入口，点击/回车即提交全部选中项 */}
          {!isMultiQuestion && isMultiSelect && (
            <button
              type="button"
              onClick={() => confirmSingleMultiSelect()}
              disabled={!canSubmitMulti}
              onMouseDown={(e) => e.preventDefault()}
              className={FOOTER_BTN_PRIMARY}
            >
              {t(lang, 'multi_select_confirm')}
            </button>
          )}
        </div>
      </div>
    </div>

    {/* 计划内容弹窗：卡片左下角"弹窗打开"触发放大形态（对齐 FileViewerModal 交互） */}
    {planPopOut && (
      <div
        className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-6"
        onClick={() => setPlanPopOut(false)}
        role="dialog"
        aria-modal="true"
      >
        <div
          onClick={(e) => e.stopPropagation()}
          className="relative bg-surface-card border border-border-light rounded-2xl shadow-card w-full max-w-3xl h-[75vh] flex flex-col overflow-hidden modal-origin-center animate-scale-in"
        >
          <div className="px-5 py-3 border-b border-border-light flex items-center gap-3 shrink-0">
            <div className="flex-1 min-w-0">
              <div className="text-sm font-semibold text-content-primary truncate">
                {t(lang, 'planReview')}
              </div>
            </div>
            <button
              onClick={() => setPlanPopOut(false)}
              title={t(lang, 'image_preview_close')}
              aria-label={t(lang, 'image_preview_close')}
              className="w-7 h-7 flex items-center justify-center rounded-lg text-content-secondary glass-option-hover hover:text-content-primary transition-colors cursor-pointer shrink-0"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                <path d="M18 6L6 18M6 6l12 12" />
              </svg>
            </button>
          </div>
          <div className="flex-1 min-h-0 overflow-y-auto px-5 py-4 select-text">
            <div className="text-sm prose prose-sm max-w-none text-content-primary">
              <ReactMarkdown remarkPlugins={[remarkGfm, remarkSuperscript]} rehypePlugins={[rehypeHighlight, rehypeRaw]}>
                {planText}
              </ReactMarkdown>
            </div>
          </div>
        </div>
      </div>
    )}
    </>
  );
}
