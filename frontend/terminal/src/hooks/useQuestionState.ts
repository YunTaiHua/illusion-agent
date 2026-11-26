/**
 * @fileoverview 多问题问答状态管理 Hook
 *
 * 本模块提供对 ask_user_question 工具多问题（1-4 个）状态的集中管理，
 * 使用 reducer 管理以下状态：
 * - currentQuestionIndex：当前展示的问题索引（等于问题数时进入复核页）
 * - answers：以问题文本为键的答案映射
 * - questionStates：以问题文本为键的每题局部状态（选中值 + 文本输入值）
 * - isInTextInput：是否正在"其他"输入框中输入（用于禁用问题间导航）
 *
 * @module useQuestionState
 */

import {useCallback, useReducer} from 'react';

/**
 * 单个问题的答案值（单选为字符串，多选为字符串数组）
 */
export type AnswerValue = string;

/**
 * 单个问题的局部状态
 */
export type QuestionState = {
	/** 当前选中值（单选为字符串，多选为字符串数组） */
	selectedValue?: string | string[];
	/** "其他"选项的文本输入内容 */
	textInputValue?: string;
	/** 自由文本多行缓冲（Shift+Enter 累积的行） */
	extraLines?: string[];
	/** 无选项自由文本单行输入 */
	freeTextInput?: string;
};

/**
 * reducer 整体状态
 */
type State = {
	/** 当前问题索引（等于问题数表示已进入复核页） */
	currentQuestionIndex: number;
	/** 答案映射：键为问题文本 */
	answers: Record<string, AnswerValue>;
	/** 每题局部状态：键为问题文本 */
	questionStates: Record<string, QuestionState>;
	/** 是否正在文本输入框中（禁用左右/Tab 导航） */
	isInTextInput: boolean;
};

/**
 * reducer 动作类型
 */
type Action =
	| {type: 'next-question'}
	| {type: 'prev-question'}
	| {
		type: 'update-question-state';
		questionText: string;
		updates: Partial<QuestionState>;
		isMultiSelect: boolean;
	}
	| {type: 'set-answer'; questionText: string; answer: string; shouldAdvance: boolean}
	| {type: 'set-text-input-mode'; isInInput: boolean}
	| {type: 'reset'};

/**
 * reducer 实现
 */
function reducer(state: State, action: Action): State {
	switch (action.type) {
		// 前进到下一题，退出文本输入模式
		case 'next-question':
			return {
				...state,
				currentQuestionIndex: state.currentQuestionIndex + 1,
				isInTextInput: false,
			};

		// 返回上一题（不小于 0）
		case 'prev-question':
			return {
				...state,
				currentQuestionIndex: Math.max(0, state.currentQuestionIndex - 1),
				isInTextInput: false,
			};

		// 更新某题的局部状态（选中值或文本输入值）
		case 'update-question-state': {
			const existing = state.questionStates[action.questionText];
			const newState: QuestionState = {
				selectedValue:
					action.updates.selectedValue !== undefined
						? action.updates.selectedValue
						: existing?.selectedValue ?? (action.isMultiSelect ? [] : undefined),
				textInputValue:
					action.updates.textInputValue !== undefined
						? action.updates.textInputValue
						: existing?.textInputValue,
				extraLines:
					action.updates.extraLines !== undefined
						? action.updates.extraLines
						: existing?.extraLines,
				freeTextInput:
					action.updates.freeTextInput !== undefined
						? action.updates.freeTextInput
						: existing?.freeTextInput,
			};
			return {
				...state,
				questionStates: {
					...state.questionStates,
					[action.questionText]: newState,
				},
			};
		}

		// 设置某题的答案，可选是否同时前进
		case 'set-answer': {
			const newState = {
				...state,
				answers: {
					...state.answers,
					[action.questionText]: action.answer,
				},
			};
			if (action.shouldAdvance) {
				return {
					...newState,
					currentQuestionIndex: newState.currentQuestionIndex + 1,
					isInTextInput: false,
				};
			}
			return newState;
		}

		// 切换文本输入模式
		case 'set-text-input-mode':
			return {
				...state,
				isInTextInput: action.isInInput,
			};

		// 重置全部状态
		case 'reset':
			return INITIAL_STATE;
	}
}

/** reducer 初始状态 */
const INITIAL_STATE: State = {
	currentQuestionIndex: 0,
	answers: {},
	questionStates: {},
	isInTextInput: false,
};

/**
 * 多问题问答状态 Hook 对外暴露的接口
 */
export type MultipleChoiceState = {
	/** 当前问题索引 */
	currentQuestionIndex: number;
	/** 答案映射 */
	answers: Record<string, AnswerValue>;
	/** 每题局部状态 */
	questionStates: Record<string, QuestionState>;
	/** 是否在文本输入框中 */
	isInTextInput: boolean;
	/** 前进到下一题 */
	nextQuestion: () => void;
	/** 返回上一题 */
	prevQuestion: () => void;
	/** 更新某题局部状态 */
	updateQuestionState: (
		questionText: string,
		updates: Partial<QuestionState>,
		isMultiSelect: boolean,
	) => void;
	/** 设置某题答案（可选是否前进） */
	setAnswer: (questionText: string, answer: string, shouldAdvance?: boolean) => void;
	/** 切换文本输入模式 */
	setTextInputMode: (isInInput: boolean) => void;
	/** 重置全部状态 */
	reset: () => void;
};

/**
 * 多问题问答状态管理 Hook
 *
 * @returns 多问题状态与一组操作函数
 */
export function useQuestionState(): MultipleChoiceState {
	const [state, dispatch] = useReducer(reducer, INITIAL_STATE);

	const nextQuestion = useCallback(() => {
		dispatch({type: 'next-question'});
	}, []);

	const prevQuestion = useCallback(() => {
		dispatch({type: 'prev-question'});
	}, []);

	const updateQuestionState = useCallback(
		(questionText: string, updates: Partial<QuestionState>, isMultiSelect: boolean) => {
			dispatch({type: 'update-question-state', questionText, updates, isMultiSelect});
		},
		[],
	);

	const setAnswer = useCallback((questionText: string, answer: string, shouldAdvance: boolean = true) => {
		dispatch({type: 'set-answer', questionText, answer, shouldAdvance});
	}, []);

	const setTextInputMode = useCallback((isInInput: boolean) => {
		dispatch({type: 'set-text-input-mode', isInInput});
	}, []);

	const reset = useCallback(() => {
		dispatch({type: 'reset'});
	}, []);

	return {
		currentQuestionIndex: state.currentQuestionIndex,
		answers: state.answers,
		questionStates: state.questionStates,
		isInTextInput: state.isInTextInput,
		nextQuestion,
		prevQuestion,
		updateQuestionState,
		setAnswer,
		setTextInputMode,
		reset,
	};
}
