(() => {
  const form = document.getElementById('pbixForm');
  const input = document.getElementById('pbixFile');
  const status = document.getElementById('pbixStatus');
  const result = document.getElementById('pbixResults');
  const button = form.querySelector('[type="submit"]');
  const search = document.getElementById('pbixSearch');
  const filter = document.getElementById('pbixFilter');
  const type = document.getElementById('pbixType');
  const loading = document.getElementById('pbixLoading');
  function setLoading(value) { loading.classList.toggle('hidden', !value); form.setAttribute('aria-busy', String(value)); button.disabled = value; button.textContent = value ? 'Analisando...' : 'Analisar'; }
  let report = null, controller = null, generation = 0;
  const labels = { used: 'É usado', unused_dependency: 'Usado por não usado', unused: 'Não é usado', review: 'Analisar' };
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function clear() {
    generation++; controller?.abort(); controller = null; report = null;
    result.classList.add('hidden'); document.getElementById('pbixRows').innerHTML = '';
    status.textContent = ''; setLoading(false); search.value = ''; filter.value = ''; type.value = '';
  }
  form.addEventListener('reset', clear);
  input.addEventListener('change', clear);
  function renderRows() {
    if (!report) return;
    const q = search.value.trim().toLocaleLowerCase('pt-BR');
    const rows = report.items.filter(i => (!filter.value || i.status === filter.value) && (!type.value || i.type === type.value) && (i.name + ' ' + i.table).toLocaleLowerCase('pt-BR').includes(q));
    document.getElementById('pbixCount').textContent = `${rows.length} de ${report.items.length} itens · use a busca e os filtros para refinar`;
    document.getElementById('pbixRows').innerHTML = rows.length ? rows.map(i => `<tr><td><span class="pbix-item-name">${escape(i.name)}</span><small>${escape(i.type)}</small></td><td>${escape(i.table)}</td><td><span class="pbix-badge ${escape(i.status)}">${labels[i.status] || 'Precisa de revisão'}</span></td><td>${escape(i.reason)}${i.evidence?.length ? `<details><summary>Ver referências (${i.evidence.length})</summary><ul>${i.evidence.map(e => `<li>${escape(e)}</li>`).join('')}</ul></details>` : ''}</td></tr>`).join('') : '<tr><td colspan="4">Nenhum item nesta seleção. Consulte também os itens que precisam de revisão.</td></tr>';
  }
  search.addEventListener('input', renderRows); filter.addEventListener('change', renderRows); type.addEventListener('change', renderRows);
  document.getElementById('pbixClearFilters').addEventListener('click', () => { search.value = ''; filter.value = ''; type.value = ''; renderRows(); });
  form.addEventListener('submit', async event => {
    event.preventDefault();
    const file = input.files[0];
    if (!file || !file.name.toLowerCase().endsWith('.pbix')) { status.textContent = 'Escolha um arquivo .pbix.'; return; }
    if (file.size > 50 * 1024 * 1024) { status.textContent = 'Esta versão aceita arquivos de até 50 MB.'; return; }
    const base = String(window.PBIX_API_URL || '').replace(/\/$/, '');
    if (!/^https:\/\//.test(base) && !/^http:\/\/(localhost|127\.0\.0\.1)(:\d+)?$/.test(base)) {
      status.textContent = 'A API Power BI ainda não foi configurada. Configure a URL em pbix-config.js após publicar o backend.'; return;
    }
    clear(); const id = generation; controller = new AbortController();
    const requestController = controller;
    const timer = setTimeout(() => requestController.abort(), 180000);
    setLoading(true); status.textContent = '';
    try {
      const { data, error } = await authClient.auth.getSession();
      if (error || !data.session?.access_token) throw new Error('Entre novamente para analisar o PBIX.');
      const body = new FormData(); body.append('file', file);
      const response = await fetch(base + '/api/pbix/extract', {method: 'POST', headers: {Authorization: 'Bearer ' + data.session.access_token}, body, signal: requestController.signal});
      let payload;
      try { payload = await response.json(); } catch { throw new Error('A API não retornou uma resposta válida. Confira se o serviço está disponível.'); }
      if (!response.ok) throw new Error(typeof payload.detail === 'string' ? payload.detail : 'Não foi possível analisar o arquivo.');
      if (id !== generation || !activeEmail) return;
      report = payload;
      const coverageBadge = document.getElementById('pbixCoverageBadge');
      coverageBadge.textContent = payload.coverage === 'limited' ? 'Leitura com limitações' : 'Análise concluída';
      coverageBadge.className = 'pbix-badge ' + (payload.coverage === 'limited' ? 'review' : 'used');
      document.getElementById('pbixFilename').textContent = payload.filename;
      document.getElementById('pbixSummary').innerHTML = [['tables','Tabelas'],['measures','Medidas'],['columns','Colunas'],['used','É usado'],['unused_dependency','Usado por não usado'],['unused','Não é usado'],['review','Analisar']].map(([key,label]) => `<div class="panel pbix-metric"><span>${label}</span><strong>${Number(payload.summary[key])}</strong></div>`).join('');
      document.getElementById('pbixWarnings').innerHTML = `<p>${escape(payload.limitations)}</p>` + (payload.warnings.length ? `<ul>${payload.warnings.map(w => `<li>${escape(w)}</li>`).join('')}</ul>` : '<p>Layout clássico e expressões lidos. Usos fora deste arquivo continuam fora do escopo.</p>');
      result.classList.remove('hidden'); renderRows();
      status.textContent = payload.coverage === 'limited' ? 'Leitura com limitações. Itens sem referência foram encaminhados para revisão.' : 'Análise concluída. Consulte o uso dos itens e valide antes de remover no Power BI.';
    } catch (error) {
      if (id === generation) status.textContent = error.name === 'AbortError' ? 'Tempo de espera excedido. Tente um PBIX menor; o servidor pode ainda estar concluindo a leitura.' : error instanceof TypeError ? 'Não foi possível acessar a API. Confira a URL, o serviço e os domínios permitidos (CORS).' : error.message;
    } finally { clearTimeout(timer); if (id === generation) { setLoading(false); controller = null; } }
  });
})();
