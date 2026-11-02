/**
 * 与 server/app/schemas.py 一一对应。
 * 后端已验证，字段名/含义不要在这里"优化"，改了就对不上。
 */

/** POST /api/screenshot 响应 */
export interface ScreenshotOut {
  image_id: string
  width: number
  height: number
}

/** POST /api/locate 请求体（image_id 8–64 字符，goal 1–100 字） */
export interface LocateIn {
  image_id: string
  goal: string
}

/** 归一化坐标 [x0, y0, x1, y1]，取值 0–1000，相对原图 */
export type NormBox = [number, number, number, number]

/** POST /api/locate 响应 */
export interface LocateOut {
  /** 命中的元素文字，如 "A.儿子" */
  text: string
  /** 原图像素坐标，已加 padding */
  bbox_px: [number, number, number, number]
  /** 0–1000 归一化坐标，前端叠圈用这个 */
  bbox_norm: NormBox
  confidence: number
  low_confidence: boolean
  /** 给老人看的一句话，≤20 字 */
  instruction: string
  reason: string
  candidate_count: number
  elapsed_ms: number
}

/** 任务链里的一步（来自 data/rag/paths.jsonl） */
export interface PathStep {
  step: number
  target: string
  hint?: string
}

/** POST /api/agent/run 请求体 */
export interface AgentRunIn {
  goal: string
  /** 按步骤顺序排列的截图 id（Web 用这个） */
  image_ids?: string[]
  /** 本地绝对路径，MCP / 调试用 */
  image_paths?: string[]
  task_id?: string
}

export type AgentStatus = 'done' | 'in_progress' | 'needs_retake' | 'escalated' | 'no_image'

/** 多步流程里的一步结果 */
export interface AgentStep {
  step: number
  text: string
  instruction: string
  bbox_norm: NormBox | null
  confidence: number
  next_step: PathStep | null
  has_next: boolean
}

/** POST /api/agent/run 响应 */
export interface AgentResult {
  status: AgentStatus
  task_id: string
  task_name: string
  risk: { risky: boolean; level: string; hits: string[]; advice: string }
  steps: AgentStep[]
  /** 每一步的决策记录，可直接渲染成"它刚才想了什么" */
  trace: Array<Record<string, unknown> & { node: string }>
  next_step: PathStep | null
}

/**
 * 后端错误语义（routes.py 实测）：
 * 400 格式不支持/空文件/图片无法解析；404 image_id 不存在；413 超过 8MB；
 * 422 业务拒答（detail 为字符串）或参数校验失败（detail 为数组）；500 服务端异常；502 模型返回越界编号。
 */
export type ApiErrorKind =
  | 'refused' // 422 业务拒答：模型说"看不出该点哪"，是正常结果不是 bug
  | 'invalid' // 422 参数校验失败 / 400 请求本身有问题
  | 'not-found' // 404 截图已被清理或从未上传
  | 'too-large' // 413
  | 'server' // 500 / 502
  | 'network' // 断网、后端没起、超时

export class ApiError extends Error {
  readonly status: number
  readonly kind: ApiErrorKind

  constructor(status: number, kind: ApiErrorKind, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.kind = kind
  }

  /** 用户该重拍还是该重试，由 kind 决定，不要靠文案判断 */
  get retakable(): boolean {
    return this.kind === 'refused' || this.kind === 'not-found' || this.kind === 'too-large'
  }
}

/** 后端没起时给一句能直接照做的提示 */
export function apiErrorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message
  if (err instanceof Error) return err.message
  return '未知错误'
}
