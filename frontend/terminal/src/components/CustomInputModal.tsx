/**
 * @fileoverview 自定义数字输入模态对话框组件
 *
 * 用于 /max-tokens 选择 "custom" 后接收用户输入的自定义令牌数。
 * 支持数字校验、Esc 取消、Enter 提交。
 *
 * @module CustomInputModal
 */

import React, {useState} from 'react';
import {Box, Text, useInput} from 'ink';
import TextInput from 'ink-text-input';

import {useTheme} from '../theme/ThemeContext.js';
import {t, UiLanguage} from '../i18n.js';

/**
 * 自定义输入模态对话框属性
 */
type CustomInputModalProps = {
	/** 提示文案 */
	prompt: string;
	/** 当前 UI 语言 */
	language: UiLanguage;
	/** 提交回调，参数为校验通过的字符串 */
	onSubmit: (value: string) => void;
	/** 取消回调 */
	onCancel: () => void;
	/** 是否校验为正整数（默认 true）；false 时接受任意非空文本（如重命名名称） */
	numeric?: boolean;
	/** 输入占位符（默认 "1024"） */
	placeholder?: string;
};

/**
 * 自定义输入模态对话框
 *
 * @param props - 组件属性
 * @returns 返回模态对话框的 JSX 元素
 */
export function CustomInputModal({
	prompt,
	language,
	onSubmit,
	onCancel,
	numeric = true,
	placeholder = '1024',
}: CustomInputModalProps): React.JSX.Element {
	const [value, setValue] = useState('');
	const [error, setError] = useState('');
	const theme = useTheme();

	useInput((_chunk, key) => {
		if (key.escape) {
			onCancel();
			return;
		}
	});

	const handleSubmit = (v: string): void => {
		const trimmed = v.trim();
		if (numeric) {
			if (!/^\d+$/.test(trimmed) || parseInt(trimmed, 10) <= 0) {
				setError(t(language, 'maxTokensInvalid'));
				return;
			}
		} else if (!trimmed) {
			setError(t(language, 'inputValueEmpty'));
			return;
		}
		onSubmit(trimmed);
	};

	return (
		<Box flexDirection="column" borderStyle="round" borderColor={theme.colors.illusion} paddingX={2} paddingY={1}>
			<Box>
				<Text color={theme.colors.illusionShimmer} bold>
					{prompt}{' '}
				</Text>
				<TextInput
					value={value}
					onChange={setValue}
					placeholder={placeholder}
					focus={true}
					showCursor={true}
					onSubmit={handleSubmit}
				/>
			</Box>
			{error ? (
				<Box marginTop={1}>
					<Text color="red">{error}</Text>
				</Box>
			) : null}
			<Box marginTop={1}>
				<Text color="gray">{t(language, 'questionHintCancel')}, {t(language, 'questionHintSubmit')}</Text>
			</Box>
		</Box>
	);
}
