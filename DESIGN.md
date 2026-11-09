---
name: 私人影院
description: 参考 Emby 浅色主题的找片与追剧管理界面
colors:
  primary: "#267939"
  primary-hover: "#20672f"
  primary-soft: "#e7f2e7"
  canvas: "#f4f5f6"
  surface: "#ffffff"
  subtle: "#f0f3f1"
  hover: "#e8eeea"
  text: "#222c29"
  muted: "#5e6b64"
  line: "#d9e0dc"
  control-line: "#839389"
  info: "#246b9b"
  info-soft: "#e8f1f8"
  warning: "#855b0b"
  warning-soft: "#fff4d9"
  danger: "#b13d39"
  danger-soft: "#fbecea"
  dark-canvas: "#121716"
  dark-surface: "#1b2320"
  dark-subtle: "#222d27"
  dark-hover: "#2c3931"
  dark-text: "#edf3ef"
  dark-muted: "#a8b8ad"
  dark-line: "#34473b"
  dark-control-line: "#6a8371"
  dark-primary: "#8bd895"
  dark-primary-hover: "#a5e3ad"
  dark-primary-soft: "#263f2d"
  dark-ink: "#142719"
  dark-info: "#90c8f0"
  dark-info-soft: "#22394a"
  dark-warning: "#f0cb79"
  dark-warning-soft: "#3a321f"
  dark-danger: "#ffaaa2"
  dark-danger-soft: "#432b29"
typography:
  body:
    fontFamily: '"Segoe UI", "Microsoft YaHei", system-ui, sans-serif'
    fontSize: "15px"
    lineHeight: 1.65
  heading:
    fontSize: "30px"
    fontWeight: 700
    lineHeight: 1.35
  brand-caption: {fontSize: "10px"}
  caption: {fontSize: "11px"}
  metadata: {fontSize: "12px"}
  label: {fontSize: "13px"}
  control: {fontSize: "14px"}
  content: {fontSize: "16px"}
  section-mobile: {fontSize: "17px"}
  section: {fontSize: "18px"}
  brand: {fontSize: "19px"}
  heading-mobile: {fontSize: "26px"}
  login: {fontSize: "28px"}
  search-mobile: {fontSize: "32px"}
  search: {fontSize: "42px"}
rounded:
  badge: "5px"
  field: "8px"
  control: "9px"
  brand-mobile: "10px"
  search-mobile: "12px"
  panel: "14px"
  dialog: "16px"
  circle: "50%"
spacing:
  small: "8px"
  medium: "16px"
  large: "24px"
---

## 1. Visual Theme & Atmosphere

以用户指定的 Emby 浅色主题为视觉依据：浅灰画布、白色面板、绿色主操作。先保证资源、版本条件、真实状态和操作易于扫描，再用图标与少量语义色增强层次。保留原有的大搜索框、顶部导航和底部 VPS 状态。

Impeccable 用于配色、排版、可访问性与实际渲染检查。Awesome Design MD 中 Spotify 的内容层次和 Raycast 的控件细节作为参考；项目配色及中文排版以 Emby 和实际任务为准。参考及许可见 SOURCES.md。

## 2. Color Palette & Roles

绿色用于主操作、当前导航、选中的资源类型和成功状态；蓝色用于下载、归档与信息；琥珀色用于待配置和暂停；红色用于异常和删除。状态必须同时保留文字。

全部颜色从 portal/style.css 的语义变量取值。深色主题单独定义背景、层级与文字。普通正文和标签对比度至少 4.5:1，输入框边界至少 3:1。

## 3. Typography Rules

使用本地系统字体。页面标题 30px，搜索首页标题 42px，分组标题 18px，内容标题 16px，表单和说明 13–15px。手机端分别缩至 26px、32px 和 15px。资源标题允许任意位置换行，数字使用等宽数字特性，说明文字控制在 72ch 内。

## 4. Component Stylings

- 按钮统一 44px 最小操作高度、9px 圆角；主按钮使用绿色，删除确认使用红色。
- 输入框有清晰边界与绿色焦点；搜索框整体显示焦点。
- SVG 图标统一 24 单位坐标、1.7 描边；图标库位于 portal/index.html，动态内容复用 node。装饰图标使用 aria-hidden，操作保留文字。
- 列表面板承载完整资源、任务或播放记录；同一任务的文件使用内部细分隔线。
- 设置按云盘、站点、服务连接、保留时间、刮削和高级管理分组。仅在服务检查确认完成后显示成功标记。
- 空列表保留引导说明和对应图标。等待、失败、未连接、删除确认及上传阶段均保留实际业务含义。

## 5. Layout Principles

内容区域最大 1184px，桌面留出 36px 内边距。搜索首页居中；结果页将搜索上移。设置使用两列，手机改为单列。1150px 以下导航移至独立一行，760px 以下使用六列图标加文字导航，320px 仍保持“看片、找片”的顺序。

## 6. Depth & Elevation

常规面板通过浅背景和 1px 边界分层。菜单、通知和原生对话框使用带位移的柔和阴影。交互反馈使用 180ms 颜色过渡，尊重减少动态效果的系统偏好。

## 7. Do's and Don'ts

保留现有服务接口、上传校验、删除确认及隐私边界。“网盘直连跳转”不能声称实际播放成功。正式页面只展示真实业务内容，演示数据仅用于本地检查。

主题使用同源 cinema-theme 本地设置。首次访问默认浅色，保留已保存的深色选择；找片页和 Emby 使用同一份 portal/theme.js，跨标签同步，重新加载后保留选择。存储被禁用时仍能切换当前页面的主题。

Emby 后续组件插入样式时，使用保留节点状态的移动方式维持主题优先级，避免重新加载已生效的主题 CSS。兼容旧浏览器的重新插入路径可能短暂重载样式；验收必须等待主界面与两份原生主题样式加载完成，检查实际文字和背景颜色。

## 8. Responsive Behavior

修改后至少检查桌面、平板和 320px/390px 手机宽度，以及长标题、空列表、错误、加载和弹窗。保留键盘焦点、跳转到主要内容、输入标签及原生对话框行为。

可运行检查：node tests/check_ui.cjs，开发环境需提供 Playwright 与本地 Chrome；可通过 NODE_PATH 指向已有安装，无需加入生产依赖。业务回归：python -m unittest discover -s tests -v。
