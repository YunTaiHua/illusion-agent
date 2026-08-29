/**
 * @fileoverview 共享图标组件
 *
 * 左栏/右栏（含折叠态按钮组）全部图标统一在此管理，页面各处只引用组件不再内联 SVG。
 * 复用场景：
 * - NewChatIcon：新建会话按钮图标（顶部按钮、目录项右侧、折叠栏）
 * - FolderClosedIcon / FolderOpenIcon：目录项的折叠/展开双图标
 *   （展开为打开文件夹、折叠为关闭文件夹）
 * - PanelLeftIcon / PanelRightIcon：左/右栏的折叠/展开面板图标
 * - ChartBarIcon：用量 tab 条形图；LayersIcon：区块 tab 层叠菱形
 * - CpuIcon / FolderOutlineIcon / SparkleIcon / GitBranchIcon / LayersIcon /
 *   SunIcon / MoonIcon / MonitorIcon / ListChecksIcon：描边风格字形
 *   线宽按 viewBox 折算对齐插件基准（16 viewBox=1.4；24 viewBox=2.1；
 *   256 viewBox=22.5），14px 显示下视觉等宽
 * - 其余通用界面图标（主题三个、删除、三个点、铅笔、齿轮、刷新、箭头、Spinner、文件）
 */

/**
 * 新会话按钮图标（聊天气泡 + 加号）
 */
export function NewChatIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path d="M8.00003 0.3237C3.76075 0.3237 0.32373 3.76072 0.32373 8C0.32373 9.17603 0.589121 10.2922 1.0632 11.2901L1.35291 11.8989L2.5705 11.3205L2.28079 10.7117C1.89079 9.89074 1.67301 8.97167 1.67301 8C1.67301 4.50546 4.50549 1.67298 8.00003 1.67298C11.4946 1.67298 14.3271 4.50546 14.3271 8C14.3271 11.4945 11.4946 14.327 8.00003 14.327C7.28473 14.327 6.76077 14.277 6.29621 14.1487C5.83857 14.0224 5.40441 13.8109 4.88514 13.4488C4.12569 12.919 3.03778 12.7316 2.141 13.2978L2.12682 13.307L2.11264 13.3171L1.34886 13.854L1.79659 15.188L2.86122 14.4384C3.19068 14.2305 3.68325 14.2542 4.11326 14.5539C4.72789 14.9826 5.30042 15.2724 5.93762 15.4484C6.56803 15.6224 7.22776 15.6763 8.00003 15.6763C12.2393 15.6763 15.6763 12.2393 15.6763 8C15.6763 3.76072 12.2393 0.3237 8.00003 0.3237ZM7.32033 4.82535V7.32536H4.82538V8.67464H7.32033V11.1747H8.6696V8.67464H11.1747V7.32536H8.6696V4.82535H7.32033Z" fill="currentColor" />
    </svg>
  );
}

/**
 * 目录展开态图标（打开文件夹）
 */
export function FolderOpenIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path d="M5.19629 1.57104C5.81144 1.5711 6.38623 1.8786 6.72754 2.39038L7.19922 3.09839C7.28454 3.22635 7.42824 3.30344 7.58203 3.30347H12.1699C13.5039 3.30348 14.5859 4.38548 14.5859 5.71948V6.62671C15.2694 7.02689 15.6605 7.85012 15.4385 8.68726L14.3848 12.658C14.1037 13.7164 13.1449 14.4527 12.0498 14.4529H2.91699C1.51651 14.4529 0.451662 13.2814 0.501954 11.9519V3.98706C0.501954 2.65305 1.58396 1.57104 2.91797 1.57104H5.19629ZM3.7793 7.75562C3.30994 7.75562 2.89883 8.07153 2.77832 8.52515L1.91602 11.7722C1.74167 12.4291 2.23734 13.073 2.91699 13.073H12.0498C12.5191 13.0728 12.9304 12.757 13.0508 12.3035L14.1045 8.33374C14.1819 8.04202 13.9619 7.756 13.6602 7.75562H3.7793ZM2.91797 2.9519C2.34625 2.9519 1.88281 3.41534 1.88281 3.98706V7.2937C2.33068 6.7269 3.02249 6.37476 3.7793 6.37476H13.2051V5.71948C13.2051 5.14777 12.7416 4.68434 12.1699 4.68433H7.58203C6.96675 4.6843 6.39209 4.37595 6.05078 3.86401L5.5791 3.15601C5.49379 3.02821 5.34995 2.95196 5.19629 2.9519H2.91797Z" fill="currentColor" />
      <path opacity="0.2" d="M13.6602 7.75525C13.9618 7.7556 14.1815 8.04179 14.1045 8.33337L13.0508 12.3031C12.9304 12.7567 12.5191 13.0725 12.0498 13.0726H2.91701C2.23744 13.0725 1.7417 12.4287 1.91603 11.7719L2.77834 8.52478C2.89898 8.07146 3.31018 7.75532 3.77931 7.75525H13.6602ZM5.1963 2.95154C5.34985 2.95159 5.49377 3.02803 5.57912 3.15564L6.0508 3.86365C6.39205 4.37553 6.96685 4.68385 7.58205 4.68396H12.1699C12.7416 4.68396 13.2049 5.14754 13.2051 5.71912V6.37439H3.77931C3.02267 6.37444 2.33067 6.72671 1.88283 7.29333V3.98669C1.88299 3.4152 2.34649 2.95168 2.91798 2.95154H5.1963Z" fill="currentColor" />
    </svg>
  );
}

/**
 * 目录折叠态图标（关闭文件夹）
 */
export function FolderClosedIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path transform="translate(1.5 2.429)" d="M5.05582 0.518756L4.50669 0.86654L5.05582 0.518756ZM13 9.4837L13.65 9.4837L13.65 3.53962L13 3.53962L12.35 3.53962L12.35 9.4837L13 9.4837ZM11.3264 1.86603L11.3264 1.21603L6.52313 1.21603L6.52313 1.86603L6.52313 2.51603L11.3264 2.51603L11.3264 1.86603ZM5.58054 1.34727L6.12968 0.999489L5.60495 0.170972L5.05582 0.518756L4.50669 0.86654L5.03141 1.69506L5.58054 1.34727ZM4.11323 1.23058e-13L4.11323 -0.65L1.67359 -0.65L1.67359 5.00699e-14L1.67359 0.65L4.11323 0.65L4.11323 1.23058e-13ZM0 1.67359L-0.65 1.67359L-0.65 9.4837L0 9.4837L0.65 9.4837L0.65 1.67359L0 1.67359ZM11.3264 11.1573L11.3264 10.5073L1.67359 10.5073L1.67359 11.1573L1.67359 11.8073L11.3264 11.8073L11.3264 11.1573ZM0 9.4837L-0.65 9.4837C-0.65 10.767 0.390308 11.8073 1.67359 11.8073L1.67359 11.1573L1.67359 10.5073C1.10828 10.5073 0.65 10.049 0.65 9.4837L0 9.4837ZM1.67359 5.00699e-14L1.67359 -0.65C0.390307 -0.65 -0.65 0.390309 -0.65 1.67359L0 1.67359L0.65 1.67359C0.65 1.10828 1.10828 0.65 1.67359 0.65L1.67359 5.00699e-14ZM5.05582 0.518756L5.60495 0.170972C5.28121 -0.340193 4.71829 -0.65 4.11323 -0.65L4.11323 1.23058e-13L4.11323 0.65C4.27282 0.65 4.4213 0.731715 4.50669 0.86654L5.05582 0.518756ZM6.52313 1.86603L6.52313 1.21603C6.36354 1.21603 6.21507 1.13431 6.12968 0.999489L5.58054 1.34727L5.03141 1.69506C5.35515 2.20622 5.91808 2.51603 6.52313 2.51603L6.52313 1.86603ZM13 3.53962L13.65 3.53962C13.65 2.25634 12.6097 1.21603 11.3264 1.21603L11.3264 1.86603L11.3264 2.51603C11.8917 2.51603 12.35 2.97431 12.35 3.53962L13 3.53962ZM13 9.4837L12.35 9.4837C12.35 10.049 11.8917 10.5073 11.3264 10.5073L11.3264 11.1573L11.3264 11.8073C12.6097 11.8073 13.65 10.767 13.65 9.4837L13 9.4837Z" fill="currentColor" />
    </svg>
  );
}

/**
 * 面板展开/折叠图标（lucide panel-left 风格：圆角矩形 + 左侧分隔竖条）。
 *
 * 用于左栏的折叠/展开按钮：
 * - 展开态点击收起左栏（竖条在左侧，语义=左侧面板）；
 * - 折叠态浮出按钮点击展开左栏（同一图标）。
 */
export function PanelLeftIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect width="18" height="18" x="3" y="3" rx="2.5" />
      <path d="M9 3v18" />
    </svg>
  );
}

/**
 * 面板展开/折叠图标（lucide panel-right 风格：圆角矩形 + 右侧分隔竖条）。
 *
 * 用于右栏的折叠/展开按钮（竖条在右侧，语义=右侧面板）。
 */
export function PanelRightIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect width="18" height="18" x="3" y="3" rx="2.5" />
      <path d="M15 3v18" />
    </svg>
  );
}

/**
 * 条形图图标（lucide bar-chart 描边风格）。
 *
 * 轴线 + 三根由低到高的竖柱，留白描边。
 * stroke 2.1 为 24 viewBox 折算值（=1.4×24/16），14px 显示下与插件图标等宽。
 */
export function ChartBarIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.1" strokeLinecap="round" strokeLinejoin="round">
      {/* 坐标轴 */}
      <path d="M3 3v18h18" />
      {/* 三根竖柱（高度递增） */}
      <path d="M8 17v-5M13 17V7M18 17v-8" />
    </svg>
  );
}

/**
 * CPU 图标（lucide cpu 描边风格）。
 *
 * 芯片封装圆角矩形 + die 内窗 + 8 根短引脚，留白描边。
 * stroke 2.1 为 24 viewBox 折算值（=1.4×24/16），14px 显示下与插件图标等宽。
 */
export function CpuIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.1" strokeLinecap="round" strokeLinejoin="round">
      {/* 芯片封装 */}
      <rect x="4" y="4" width="16" height="16" rx="2" />
      {/* die 内窗 */}
      <rect x="9" y="9" width="6" height="6" />
      {/* 8 根引脚（上下左右各两根） */}
      <path d="M9 1v3M15 1v3M9 20v3M15 20v3M1 9h3M1 15h3M20 9h3M20 15h3" />
    </svg>
  );
}

/**
 * Git 分支图标（Phosphor git-branch 静态样式）。
 *
 * 主干与分支肘部连线 + 三个圆环；头部节点 = 外环 + 实心插塞，
 * 静止态与原字形一致（实心分支头）；仅取静态字形，不含动画/交互特性。
 * stroke 22.5 为 256 viewBox 折算值（=2.1×256/24），14px 显示下与插件图标等宽。
 */
export function GitBranchIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 256 256" fill="none" stroke="currentColor" strokeWidth="22.5">
      {/* 主干（连接左侧上下两个圆环） */}
      <path d="M80,88V168" />
      {/* 分支肘部（伸向头部节点） */}
      <path d="M80,128H184A16,16,0,0,0,200,112V72" />
      {/* 左侧上方圆环 */}
      <circle cx="80" cy="64" r="24" />
      {/* 左侧下方圆环 */}
      <circle cx="80" cy="192" r="24" />
      {/* 头部节点：外环 + 实心插塞（r17 略大于洞径 r16，避免共用边产生发丝缝） */}
      <circle cx="200" cy="64" r="24" />
      <circle cx="200" cy="64" r="17" fill="currentColor" stroke="none" />
    </svg>
  );
}

/**
 * 火花/技能图标（lucide sparkle 描边风格，仅四芒星）。
 *
 * 仅中间四角星（去掉右上小十字标记），留白描边。
 * stroke 2.1 为 24 viewBox 折算值（=1.4×24/16），14px 显示下与插件图标等宽。
 */
export function SparkleIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.1" strokeLinecap="round" strokeLinejoin="round">
      {/* 四芒星 */}
      <path d="M12 3l1.9 5.8a2 2 0 0 0 1.3 1.3L21 12l-5.8 1.9a2 2 0 0 0-1.3 1.3L12 21l-1.9-5.8a2 2 0 0 0-1.3-1.3L3 12l5.8-1.9a2 2 0 0 0 1.3-1.3z" />
    </svg>
  );
}

/**
 * 目录树图标（Material folder 描边风格）。
 *
 * 分层文件夹轮廓：大文件夹 + 附带标签，留白描边。
 * stroke 2.1 为 24 viewBox 折算值（=1.4×24/16），14px 显示下与插件图标等宽。
 */
export function FolderOutlineIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.1" strokeLinejoin="round">
      {/* 主文件夹轮廓（圆角，含可编辑角标签层） */}
      <path d="M2.75 8.623v7.379a4 4 0 0 0 4 4h10.5a4 4 0 0 0 4-4v-5.69a4 4 0 0 0-4-4H12" />
      {/* 附带标签（从主框左侧伸出的路径细节） */}
      <path d="M2.75 8.624V6.998a3 3 0 0 1 3-3h2.9a2.5 2.5 0 0 1 1.768.732L12 6.313" />
      {/* 标签内部弧线（延续主轮廓折角） */}
      <path d="M2.75 8.624H8.654a2.5 2.5 0 0 0 1.768-.732L12 6.313" />
    </svg>
  );
}

// ============================================================
// 通用界面图标（左栏/右栏共用，含折叠态按钮组）
// ============================================================

/**
 * 会话文件图标（Phosphor chat-dots 描边版，不填色）。
 *
 * 气泡取描边（outline）呈现：圆角气泡轮廓 + 三个实心小圆点（底部带小尾尖）。
 * stroke 22.5 为 256 viewBox 折算值（=2.1×256/24），14px 显示下与插件图标等宽。
 */
export function ListChecksIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 256 256" fill="none" stroke="currentColor" strokeWidth="22.5" strokeLinecap="round" strokeLinejoin="round">
      {/* 圆角气泡（底部带小尾尖），描边而非填色 */}
      <path d="M216,48H40A16,16,0,0,0,24,64V224a15.84,15.84,0,0,0,9.25,14.5A16.05,16.05,0,0,0,40,240a15.89,15.89,0,0,0,10.25-3.78l.09-.07L83,208H216a16,16,0,0,0,16-16V64A16,16,0,0,0,216,48Z" />
      {/* 三个输入点（实心小圆） */}
      <circle cx="84" cy="128" r="14" fill="currentColor" stroke="none" />
      <circle cx="128" cy="128" r="14" fill="currentColor" stroke="none" />
      <circle cx="172" cy="128" r="14" fill="currentColor" stroke="none" />
    </svg>
  );
}

/** MCP 服务器（链路/连接）图标 */
export function McpIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path d="M9.94133 6.50173C11.3218 7.99603 11.3218 10.3011 9.94128 11.7954C9.88691 11.8542 9.82125 11.9196 9.72099 12.0198L7.75707 13.9838C7.65709 14.0838 7.592 14.1491 7.53334 14.2034C6.03906 15.5843 3.7327 15.5854 2.23827 14.2048C2.17933 14.1503 2.11374 14.0844 2.01315 13.9838C1.91318 13.8839 1.84922 13.8188 1.79495 13.7601C0.413857 12.2657 0.413909 9.95948 1.795 8.46503C1.84923 8.4064 1.91335 8.34115 2.01321 8.24129L3.79275 6.46313C3.71814 7.08101 3.75236 7.71445 3.90115 8.33518L3.00344 9.23151C2.89398 9.34097 2.8535 9.38307 2.82251 9.41658C1.93771 10.3744 1.93704 11.8514 2.82179 12.8092C2.85279 12.8427 2.89383 12.884 3.0034 12.9936C3.11272 13.1029 3.15429 13.1442 3.18777 13.1752C4.14561 14.0603 5.62381 14.0608 6.58178 13.1758C6.61532 13.1448 6.65722 13.1032 6.76685 12.9935L8.73077 11.0296C8.83999 10.9204 8.88142 10.8787 8.91238 10.8452C9.79744 9.88728 9.7969 8.40911 8.91173 7.45124C8.88074 7.41775 8.83944 7.3762 8.73011 7.26687C8.62082 7.15757 8.58061 7.11623 8.54712 7.08526C8.37347 6.92477 8.18243 6.79361 7.98088 6.69165L9.00289 5.66964C9.17506 5.78373 9.34035 5.91265 9.49663 6.05703C9.55538 6.11135 9.62026 6.17652 9.72036 6.27662C9.82094 6.3772 9.88686 6.4428 9.94133 6.50173Z" fill="currentColor" />
      <path d="M6.06816 9.49196C4.68626 7.99724 4.68667 5.68942 6.06885 4.19487C6.12268 4.13671 6.18789 4.07306 6.28706 3.9739L8.24541 2.01416C8.34478 1.91479 8.41018 1.85055 8.46845 1.79665C9.96301 0.414902 12.2689 0.414922 13.7635 1.79665C13.8217 1.85051 13.8866 1.91559 13.9858 2.01486C14.0849 2.11394 14.1502 2.17769 14.204 2.23583C15.5861 3.7304 15.5866 6.03823 14.2047 7.53291C14.1508 7.59125 14.0854 7.65638 13.9858 7.75595L12.1994 9.54098C12.2614 8.92982 12.2185 8.30587 12.0634 7.69657L12.9956 6.76573C13.1044 6.65692 13.1458 6.61529 13.1765 6.58205C14.0621 5.62404 14.0621 4.1454 13.1765 3.18738C13.1458 3.15419 13.104 3.1135 12.9956 3.00508C12.8877 2.89716 12.8471 2.85551 12.814 2.82485C11.8559 1.9389 10.376 1.93886 9.41794 2.82485C9.38479 2.85554 9.34381 2.89622 9.23564 3.00439L7.27728 4.96413C7.16875 5.07265 7.12708 5.11322 7.09636 5.14643C7.21074 6.10441 7.21153 7.58236 7.09705 8.5404C7.12775 8.57357 7.16826 8.61575 7.27659 8.72408C7.38456 8.83205 7.42647 8.87227 7.45958 8.90293C7.62849 9.0591 7.81309 9.1881 8.00856 9.28894L6.98795 10.3095C6.82111 10.1978 6.66052 10.0715 6.50872 9.93114C6.45057 9.87733 6.38547 9.81341 6.28637 9.71431C6.1871 9.61504 6.12202 9.55018 6.06816 9.49196Z" fill="currentColor" />
    </svg>
  );
}

/** 插件图标（2×2 应用网格 + 圆形/三角形/加号角标） */
export function PluginsIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
      {/* 2×2 应用网格：左上方形、右上圆形、左下三角形、右下加号 */}
      <rect x="1.5" y="1.5" width="5" height="5" rx="0.8" />
      <circle cx="12" cy="4" r="2.5" />
      <path d="M4 9.5L6.4 14.2H1.6Z" />
      <path d="M12 9.5v5M9.5 12h5" />
    </svg>
  );
}

/** 规则图标（清单列 + 圆点） */
export function RulesIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 14 14" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path d="M13.3277 9.69629V10.976H7.28086V9.69629H13.3277Z" fill="currentColor" />
      <path d="M13.3277 2.97256V4.25225H7.28086V2.97256H13.3277Z" fill="currentColor" />
      <path d="M4.64512 10.336C4.64505 9.62755 4.07081 9.05322 3.3623 9.05322C2.65386 9.05329 2.07956 9.62759 2.07949 10.336C2.07949 11.0445 2.65382 11.6188 3.3623 11.6188C4.07085 11.6188 4.64512 11.0446 4.64512 10.336ZM5.92559 10.336C5.92559 11.7515 4.77777 12.8993 3.3623 12.8993C1.94689 12.8993 0.799805 11.7515 0.799805 10.336C0.799871 8.92066 1.94693 7.7736 3.3623 7.77354C4.77773 7.77354 5.92552 8.92062 5.92559 10.336Z" fill="currentColor" />
      <path d="M4.64531 3.6123C4.6453 2.90382 4.07098 2.32949 3.3625 2.32949C2.65403 2.32951 2.0797 2.90383 2.07969 3.6123C2.07969 4.32079 2.65402 4.8951 3.3625 4.89512C4.07099 4.89512 4.64531 4.3208 4.64531 3.6123ZM5.925 3.6123C5.925 5.02772 4.77792 6.1748 3.3625 6.1748C1.9471 6.17479 0.8 5.02771 0.8 3.6123C0.800013 2.19691 1.9471 1.04982 3.3625 1.0498C4.77791 1.0498 5.92499 2.1969 5.925 3.6123Z" fill="currentColor" />
    </svg>
  );
}

/** 区块视图（lucide layers 三段层叠菱形，用量/区块 tab 切换用） */
export function LayersIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.1" strokeLinecap="round" strokeLinejoin="round">
      {/* 顶层 */}
      <path d="m12.83 2.18a2 2 0 0 0-1.66 0L2.6 6.08a1 1 0 0 0 0 1.83l8.58 3.91a2 2 0 0 0 1.66 0l8.58-3.9a1 1 0 0 0 0-1.83Z" />
      {/* 中层 */}
      <path d="m22 12.65-9.17 4.16a2 2 0 0 1-1.66 0L2 12.65" />
      {/* 底层 */}
      <path d="m22 17.65-9.17 4.16a2 2 0 0 1-1.66 0L2 17.65" />
    </svg>
  );
}

/** 主题：浅色（太阳） */
export function SunIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="8" cy="8" r="3" />
      <path d="M8 1.5v1.5M8 13v1.5M1.5 8h1.5M13 8h1.5M3.4 3.4l1.1 1.1M11.5 11.5l1.1 1.1M3.4 12.6l1.1-1.1M11.5 4.5l1.1-1.1" />
    </svg>
  );
}

/** 主题：深色（月亮） */
export function MoonIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
      <path d="M13 8.5a5 5 0 0 1-5.5-5.5 5 5 0 1 0 5.5 5.5z" />
    </svg>
  );
}

/** 主题：跟随系统（显示器） */
export function MonitorIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
      <rect x="2" y="3" width="12" height="8" rx="1" />
      <path d="M6 13h4M8 11v2" />
    </svg>
  );
}

/** 删除（垃圾箱） */
export function TrashIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="3 6 5 6 21 6" />
      <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
      <line x1="10" y1="11" x2="10" y2="17" />
      <line x1="14" y1="11" x2="14" y2="17" />
    </svg>
  );
}

/** 会话操作（三个点） */
export function DotsIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 16 16" fill="currentColor">
      <circle cx="8" cy="3.5" r="1.5" />
      <circle cx="8" cy="8" r="1.5" />
      <circle cx="8" cy="12.5" r="1.5" />
    </svg>
  );
}

/** 重命名（铅笔） */
export function PenIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M17 3a2.828 2.828 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z" />
    </svg>
  );
}

/** 设置（齿轮，lucide settings 描边风格：8 齿圆角外沿 + 中心孔） */
export function GearIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z" />
    </svg>
  );
}

/** 刷新（Material refresh 静态字形：双向环形箭头涂装，不带旋转动画） */
export function RefreshIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24">
      <path fill="currentColor" d="M19 8l-4 4h3c0 3.31-2.69 6-6 6c-1.01 0-1.97-.25-2.8-.7l-1.46 1.46C8.97 19.54 10.43 20 12 20c4.42 0 8-3.58 8-8h3l-4-4zM6 12c0-3.31 2.69-6 6-6c1.01 0 1.97.25 2.8.7l1.46-1.46C15.03 4.46 13.57 4 12 4c-4.42 0-8 3.58-8 8H1l4 4l4-4H6z" />
    </svg>
  );
}

/** 展开指示（右向实心三角，展开时加 rotate-90） */
export function ChevronRightIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 14 14" fill="none">
      <path d="M4.25 2.82782L4.25 11.1722C4.25 11.6622 4.84243 11.9076 5.18891 11.5611L9.36109 7.38891C9.57588 7.17412 9.57588 6.82588 9.36109 6.61109L5.18891 2.43891C4.84243 2.09243 4.25 2.33782 4.25 2.82782Z" fill="currentColor" />
    </svg>
  );
}

/** 展开/收起列表（向下细箭头，展开时 rotate-180） */
export function ChevronDownIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 4.5L6 7.5L9 4.5" />
    </svg>
  );
}

/** 加载中（旋转圆环） */
export function SpinnerIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none">
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
    </svg>
  );
}

/** 文件（可指定颜色；未指定时用主题禁用色） */
export function FileIcon({ className, color }: { className?: string; color?: string }) {
  return (
    <svg className={className} viewBox="0 0 16 16" fill="none" stroke={color ?? 'currentColor'} style={color ? { color } : undefined} strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
      <path d="M9 1.5H4A1.5 1.5 0 0 0 2.5 3v10A1.5 1.5 0 0 0 4 14.5h8a1.5 1.5 0 0 0 1.5-1.5V6L9 1.5z" className={!color ? 'text-content-disabled' : undefined} />
      <path d="M8.75 1.75V6h4.5" className={!color ? 'text-content-disabled' : undefined} />
    </svg>
  );
}

/** 会话文件行（修改文件铅笔） */
export function ModifiedFileIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
      <path d="M4 12.4l.4-1.9 7-7a1.06 1.06 0 0 1 1.5 0l.6.6a1.06 1.06 0 0 1 0 1.5l-7 7-1.9.4z" />
      <path d="M10.7 5.7l.6.6" />
    </svg>
  );
}

// ============================================================
// 通用线性图标（消息操作 / 输入框等跨组件复用）
// ============================================================

/** 加号（新建/添加） */
export function PlusIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
      <path d="M8 3v10M3 8h10" />
    </svg>
  );
}

/** 对勾（复制成功/选中态） */
export function CheckIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="square">
      <path d="M5 11.9657L8.37838 14.7529L15 5.83398" />
    </svg>
  );
}

/** 复制（双矩形） */
export function CopyIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M6.2513 6.24935V2.91602H17.0846V13.7493H13.7513M13.7513 6.24935V17.0827H2.91797V6.24935H13.7513Z" />
    </svg>
  );
}

/** 回退（撤销：反弧线 + 箭头，与 lucide rotate-ccw 同形） */
export function RewindIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 7v6h6" />
      <path d="M21 17a9 9 0 0 0-15-6.7L3 13" />
    </svg>
  );
}

/** 重新生成（旋转箭头） */
export function RegenerateIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 12a9 9 0 1 1-9-9c2.52 0 4.93 1 6.74 2.74L21 8" />
      <path d="M21 3v5h-5" />
    </svg>
  );
}

/** 停止（发送态方块） */
export function StopIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 10 10" fill="currentColor">
      <rect width="10" height="10" rx="1.5" />
    </svg>
  );
}

/** 地球图标（Browser Use 实时画面卡片入口按钮） */
export function GlobeIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="12" cy="12" r="10" />
      <line x1="2" y1="12" x2="22" y2="12" />
      <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" />
    </svg>
  );
}
