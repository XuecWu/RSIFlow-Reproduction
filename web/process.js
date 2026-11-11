const labels = {success: '成功', failed: '失败', timeout: '超时', blocked: '阻断', queued: '排队', running: '运行中'};
const label = (value) => labels[value] || value || '未知';

async function loadProcess() {
  const response = await fetch('data/process.json', {cache: 'no-store'});
  if (!response.ok) throw new Error(`process.json HTTP ${response.status}`);
  const data = await response.json();
  const counts = data.operation_counts || {};
  const cards = [
    ['全部操作', data.operations_total, 'sequence 连续记录'],
    ['关键结果', data.results_total, '带证据与复核状态'],
    ['成功', counts.success || 0, '已执行操作'],
    ['失败 / 超时', (counts.failed || 0) + (counts.timeout || 0), '保留用于审计'],
  ];
  document.getElementById('summary-cards').innerHTML = cards.map(([title, value, note]) => `
    <article class="summary-card"><span>${title}</span><strong>${value}</strong><small>${note}</small></article>`).join('');
  const run = (data.runs || []).at(-1) || {};
  document.getElementById('run-title').textContent = run.run_id || '尚未建立正式 run';
  document.getElementById('run-status').textContent = `${run.backbone || 'Qwen3-4B'} · ${run.protocol || 'protocol_reconstruction'} · ${run.status || data.current_phase || '准备中'}`;
  document.getElementById('run-provider').textContent = `${run.provider || 'OpenAI'} / ${run.model || 'gpt-6-astra'}`;
  document.getElementById('run-sco').textContent = run.sco_job_id ? `SCO: ${run.sco_job_id}` : 'SCO: 未提交正式实验';
  document.getElementById('process-generated').textContent = `静态快照 · 更新: ${data.generated_at} · 截至 sequence #${data.as_of_sequence || 0}`;
  document.getElementById('process-timeline').innerHTML = (data.timeline || []).slice().reverse().map((row) => `
    <article class="timeline-row ${row.status}">
      <span class="timeline-sequence">#${row.sequence}</span>
      <div><strong>${row.action}</strong><small>${row.phase} · ${row.timestamp || ''}</small></div>
      <span class="status-badge">${label(row.status)}</span>
    </article>`).join('');
  const tbody = document.querySelector('#process-results tbody');
  tbody.innerHTML = (data.key_results || []).slice().reverse().map((row) => `
    <tr><td>${row.phase}</td><td>${row.metric}</td><td>${row.value}</td><td>${row.review_status}</td><td>${(row.evidence_paths || []).map((path) => `<code>${path}</code>`).join('<br>')}</td></tr>`).join('');
}

loadProcess().catch((error) => {
  document.getElementById('process-timeline').textContent = `过程数据读取失败: ${error.message}`;
});
