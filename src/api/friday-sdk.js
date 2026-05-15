/* ───────────────────────────────────────────────
   星期五前端 SDK (friday.js) —— 轻量级客户端库
   支持：ES Module / CDN / 直接引入
   用法：
     const friday = new FridayClient({ baseUrl: 'http://localhost:8000' })
     friday.submit('帮我做PPT').then(console.log)
     friday.stream(workflowId, (event) => console.log(event))
   ─────────────────────────────────────────────── */

class FridayClient {
  constructor(options = {}) {
    this.baseUrl = (options.baseUrl || 'http://localhost:8000').replace(/\/$/, '')
    this.apiPath = options.apiPath || '/api/v1'
    this.token = options.token || ''
    this.headers = {
      'Content-Type': 'application/json',
      ...(this.token ? { 'Authorization': `Bearer ${this.token}` } : {}),
      ...options.headers,
    }
  }

  /* ── 核心 API ── */
  async _fetch(method, path, body) {
    const res = await fetch(`${this.baseUrl}${this.apiPath}${path}`, {
      method,
      headers: this.headers,
      body: body ? JSON.stringify(body) : undefined,
    })
    if (!res.ok) throw new FridayError(res.status, await res.text())
    return res.json()
  }

  /* ── 工作流 ── */
  async submit(task, options = {}) {
    return this._fetch('POST', '/workflows', {
      task,
      user_id: options.userId || 'default',
      mode: options.mode || 'auto',
      context: options.context || null,
    })
  }

  async getWorkflow(id) {
    return this._fetch('GET', `/workflows/${id}`)
  }

  async listWorkflows(status = '', limit = 20) {
    return this._fetch('GET', `/workflows?status=${status}&limit=${limit}`)
  }

  /* ── 多步骤工作流引擎 ── */
  async createEngineWorkflow(name, steps) {
    return this._fetch('POST', '/engine/workflows', { name, steps })
  }

  async getEngineWorkflow(id) {
    return this._fetch('GET', `/engine/workflows/${id}`)
  }

  async submitInput(wfId, stepId, data) {
    return this._fetch('POST', `/engine/workflows/${wfId}/input`, { step_id: stepId, data })
  }

  async approve(wfId, stepId, approved = true, comment = '') {
    return this._fetch('POST', `/engine/workflows/${wfId}/approve`, {
      step_id: stepId, approved, comment,
    })
  }

  async goBack(wfId, toStepId = '') {
    return this._fetch('POST', `/engine/workflows/${wfId}/back${toStepId ? `?to_step_id=${toStepId}` : ''}`)
  }

  /* ── SSE 实时流 ── */
  stream(workflowId, onEvent, onError) {
    const url = `${this.baseUrl}${this.apiPath}/stream/${workflowId}`
    const es = new EventSource(url)
    es.onmessage = (e) => {
      try {
        const event = JSON.parse(e.data)
        onEvent(event)
      } catch (err) {
        console.warn('[Friday] Failed to parse SSE event:', err)
      }
    }
    es.onerror = (err) => {
      if (onError) onError(err)
      es.close()
    }
    return {
      close: () => es.close(),
      eventSource: es,
    }
  }

  /* ── Agent ── */
  async createAgent(name, systemPrompt, model = 'deepseek-chat', tools = []) {
    return this._fetch('POST', '/agents', {
      name, system_prompt: systemPrompt, model, tools,
    })
  }

  async listAgents() {
    return this._fetch('GET', '/agents')
  }

  /* ── Skill ── */
  async listSkills() {
    return this._fetch('GET', '/skills')
  }

  async getSkill(name) {
    return this._fetch('GET', `/skills/${name}`)
  }

  /* ── 工具 ── */
  async listTools() {
    return this._fetch('GET', '/tools')
  }

  /* ── 统计 ── */
  async getStats() {
    return this._fetch('GET', '/stats')
  }

  async getHealth() {
    return this._fetch('GET', '/health/ready')
  }

  /* ── 成本 ── */
  async getCost() {
    return this._fetch('GET', '/cost')
  }
}

class FridayError extends Error {
  constructor(status, message) {
    super(`Friday API Error ${status}: ${message}`)
    this.status = status
  }
}

/* ── React Hook (可选) ── */
function useFriday(options = {}) {
  const [workflow, setWorkflow] = React.useState(null)
  const [status, setStatus] = React.useState('idle') // idle | running | awaiting_input | error
  const [error, setError] = React.useState(null)
  const [progress, setProgress] = React.useState({})
  const clientRef = React.useRef(null)

  React.useEffect(() => {
    clientRef.current = new FridayClient(options)
  }, [])

  const submit = async (task) => {
    setStatus('running')
    try {
      const result = await clientRef.current.submit(task)
      setWorkflow(result)
      setStatus('idle')
      return result
    } catch (e) {
      setError(e)
      setStatus('error')
      throw e
    }
  }

  const submitEngine = async (name, steps) => {
    setStatus('running')
    const wf = await clientRef.current.createEngineWorkflow(name, steps)
    setWorkflow(wf)

    // 连接 SSE 实时流
    const sub = clientRef.current.stream(wf.id, (event) => {
      if (event.type === 'workflow-step') {
        setWorkflow(prev => {
          if (!prev) return prev
          const steps = prev.steps.map(s =>
            s.id === event.stepId ? { ...s, status: event.status, output: event.output } : s
          )
          return { ...prev, steps }
        })
        if (event.status === 'completed' || event.status === 'error') {
          setProgress(prev => ({ ...prev, [event.stepId]: event.status }))
        }
      }
      if (event.type === 'finish') {
        setStatus('idle')
        sub.close()
      }
    })

    return wf
  }

  const approveCurrent = async (approved = true, comment = '') => {
    if (!workflow) return
    const step = workflow.steps[workflow.current_step_index]
    if (!step) return
    return clientRef.current.approve(workflow.id, step.id, approved, comment)
  }

  const goBack = async () => {
    if (!workflow) return
    return clientRef.current.goBack(workflow.id)
  }

  return {
    client: clientRef.current,
    workflow,
    status,
    error,
    progress,
    submit,
    submitEngine,
    approveCurrent,
    goBack,
  }
}

// 导出
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { FridayClient, FridayError, useFriday }
} else if (typeof window !== 'undefined') {
  window.FridayClient = FridayClient
  window.FridayError = FridayError
  window.useFriday = useFriday
}
