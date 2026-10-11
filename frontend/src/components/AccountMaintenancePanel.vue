<template>
  <section class="maintenance-panel" aria-label="账号登录维护">
    <div class="maintenance-heading">
      <div><h2>账号登录维护</h2><p>抖音 · 小红书 · 视频号。只读检测与会话更新不保证永久在线。</p></div>
      <el-button @click="refresh" :loading="loading">刷新维护状态</el-button>
    </div>
    <div class="maintenance-controls">
      <el-switch v-model="settings.enabled" active-text="后台定时维护" aria-label="后台定时维护" />
      <span>正常检测间隔</span>
      <el-input-number v-model="settings.intervalMinutes" :min="15" :max="1440" :step="15" aria-label="维护间隔分钟" />
      <span>分钟 · 同时最多检测 1 个账号</span>
      <el-button type="primary" @click="save" :loading="saving">保存维护设置</el-button>
    </div>
    <p class="maintenance-help">确认失效或需要人工验证后暂停自动检测；网络异常保留原凭据并延后重试。发布、登录、同步与维护共用账号锁。</p>
    <el-table :data="accounts" empty-text="添加三平台账号后即可查看维护状态" size="small">
      <el-table-column prop="userName" label="账号" min-width="110" />
      <el-table-column label="维护" width="70"><template #default="{ row }">
        <el-switch :model-value="!!row.enabled" @change="value => toggle(row, value)" :aria-label="`${row.userName}自动维护`" />
      </template></el-table-column>
      <el-table-column label="状态" min-width="145"><template #default="{ row }">
        <el-tag :type="row.state === 'valid' ? 'success' : row.state === 'error' ? 'warning' : 'info'">{{ labels[row.state] || row.state }}</el-tag>
      </template></el-table-column>
      <el-table-column label="最近检测" min-width="165"><template #default="{ row }">{{ date(row.last_check) }}</template></el-table-column>
      <el-table-column label="最近成功" min-width="165"><template #default="{ row }">{{ date(row.last_success) }}</template></el-table-column>
      <el-table-column label="操作" min-width="180"><template #default="{ row }">
        <el-button size="small" @click="check(row)" :loading="checking === row.id">检测</el-button>
        <el-button size="small" @click="relogin(row)">{{ row.state === 'verification_required' ? '人工验证' : '重新扫码' }}</el-button>
      </template></el-table-column>
    </el-table>
    <div v-if="jobs.length" class="held-jobs">
      <h3>待处理发布任务</h3>
      <p>扫码成功不会自动发布。仅未进入发布流程的任务可恢复；结果不明确的任务须先到平台核对，不能在这里重发。</p>
      <el-table :data="jobs" size="small">
        <el-table-column prop="title" label="标题" min-width="140" />
        <el-table-column label="平台" width="100"><template #default="{ row }">{{ platforms[row.platform] }}</template></el-table-column>
        <el-table-column prop="accountNames" label="账号" min-width="120" />
        <el-table-column label="状态" width="120"><template #default="{ row }">{{ jobLabels[row.state] || row.state }}</template></el-table-column>
        <el-table-column prop="message" label="说明" min-width="230" />
        <el-table-column label="操作" width="180"><template #default="{ row }">
          <el-button size="small" type="primary" :disabled="row.state !== 'waiting'" @click="resume(row)" :loading="resuming === row.id">恢复发布</el-button>
          <el-button v-if="row.state === 'waiting'" size="small" @click="cancel(row)">取消</el-button>
        </template></el-table-column>
      </el-table>
    </div>
  </section>
</template>

<script setup>
import { onMounted, onBeforeUnmount, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { http } from '@/utils/request'
const emit = defineEmits(['relogin', 'checked'])
const settings = reactive({ enabled: false, intervalMinutes: 60 })
const accounts = ref([]), jobs = ref([]), loading = ref(false), saving = ref(false)
const checking = ref(null), resuming = ref(null)
const labels = { unknown: '待检测', valid: '登录有效', expired: '已失效 · 需扫码', verification_required: '需要人工验证', missing: '登录文件缺失', error: '检测异常 · 未判失效', busy: '账号正在使用' }
const jobLabels = { waiting: '等待处理', unknown: '结果不明确', pending: '排队中', ready: '恢复排队中', running: '发布中', cancelled: '已取消' }
const platforms = { 1: '小红书', 2: '视频号', 3: '抖音' }
let timer, initialized = false
const date = value => value ? new Date(value * 1000).toLocaleString() : '尚无记录'
async function refresh() {
  if (loading.value) return
  loading.value = true
  try {
    const response = await http.get('/api/account-maintenance')
    accounts.value = response.data.accounts
    jobs.value = response.data.jobs.filter(j => j.state !== 'cancelled')
    if (!initialized) { Object.assign(settings, response.data.config); initialized = true }
  } finally { loading.value = false }
}
async function save() {
  saving.value = true
  try {
    await http.post('/api/account-maintenance/config', settings)
    ElMessage.success('维护设置已保存')
    await refresh()
  } finally { saving.value = false }
}
async function toggle(row, enabled) {
  await http.post(`/api/account-maintenance/${row.id}`, { enabled })
  await refresh()
}
async function check(row) {
  checking.value = row.id
  try {
    const result = await http.get('/checkAccount', { id: row.id })
    ElMessage({ type: result.data.valid ? 'success' : 'warning', message: result.msg })
    emit('checked')
    await refresh()
  } finally { checking.value = null }
}
function relogin(row) { emit('relogin', { id: row.id, name: row.userName, platform: platforms[row.type] }) }
async function resume(row) {
  resuming.value = row.id
  try {
    await http.post(`/api/account-maintenance/jobs/${row.id}/resume`)
    ElMessage.success('原任务已进入恢复检查，请勿新建重复任务')
    await refresh()
  } finally { resuming.value = null }
}
async function cancel(row) {
  await http.post(`/api/account-maintenance/jobs/${row.id}/cancel`)
  await refresh()
}
onMounted(() => { refresh().catch(() => {}); timer = setInterval(() => refresh().catch(() => {}), 15000) })
onBeforeUnmount(() => clearInterval(timer))
</script>

<style scoped>
.maintenance-panel { padding: 20px; margin-bottom: 24px; border: 1px solid var(--border); border-radius: 14px; background: var(--bg-elevated); color: var(--text-primary); }
.maintenance-heading { display: flex; justify-content: space-between; align-items: center; gap: 16px; }
h2 { font-size: 18px; margin: 0 0 8px; }
h3 { font-size: 16px; margin: 20px 0 8px; }
p { color: var(--text-secondary, #666); font-size: 13px; line-height: 1.6; margin: 6px 0 14px; }
.maintenance-controls { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; font-size: 13px; margin: 16px 0; }
.held-jobs { border-top: 1px solid var(--border); margin-top: 20px; }
</style>
