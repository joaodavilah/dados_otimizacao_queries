/* Análise estática local: não executa SQL e não valida um dialeto completo. */
(function () {
  function analyzeSql(source) {
    const issues = [], literals = [];
    const chars = source.split('');
    const mask = (start, end) => { for (let j = start; j < end; j++) if (chars[j] !== '\n' && chars[j] !== '\r') chars[j] = ' '; };
    const add = (id, title, message, start, end, penalty = 0, blocking = false) => {
      issues.push({ id, title, message, start, end, penalty, blocking,
        line: source.slice(0, start).split('\n').length,
        snippet: source.slice(start, end) });
    };
    let i = 0;
    while (i < source.length) {
      const start = i;
      if (source.startsWith('--', i)) {
        while (i < source.length && source[i] !== '\n') i++;
        mask(start, i);
      } else if (source.startsWith('/*', i)) {
        i += 2; let depth = 1;
        while (i < source.length && depth) {
          if (source.startsWith('/*', i)) { depth++; i += 2; }
          else if (source.startsWith('*/', i)) { depth--; i += 2; }
          else i++;
        }
        mask(start, i);
        if (depth) add('structure', 'Comentário não fechado', 'Feche o comentário com */.', start, Math.min(start + 60, i), 0, true);
      } else if (['\'', '"', '`', '['].includes(source[i])) {
        const quote = source[i], close = quote === '[' ? ']' : quote;
        i++; let closed = false;
        while (i < source.length) {
          if (source[i] === close) {
            if (source[i + 1] === close) { i += 2; continue; }
            i++; closed = true; break;
          }
          if (source[i] === '\\' && quote !== '[') i += Math.min(2, source.length - i);
          else i++;
        }
        if (quote === '\'') literals.push({ start, end: i, value: source.slice(start + 1, i - 1) });
        mask(start, i);
        // Identificadores delimitados conservam a posição e funcionam como uma coluna.
        if (quote !== '\'') chars[start] = 'x';
        if (!closed) add('structure', 'Aspas não fechadas', 'Confira o fechamento do texto ou identificador entre aspas.', start, Math.min(start + 60, i), 0, true);
      } else i++;
    }
    const sql = chars.join('');
    const stack = [];
    for (let j = 0; j < sql.length; j++) {
      if (sql[j] === '(') stack.push(j);
      if (sql[j] === ')') {
        if (stack.length) stack.pop();
        else add('structure', 'Parêntese sem abertura', 'Confira este parêntese de fechamento.', j, j + 1, 0, true);
      }
    }
    stack.forEach(j => add('structure', 'Parêntese não fechado', 'Adicione o fechamento correspondente.', j, j + 1, 0, true));
    if (!/^\s*(SELECT|WITH)\b/i.test(sql) || !/\bSELECT\b/i.test(sql))
      add('scope', 'Consulta fora do escopo', 'Cole uma única consulta SELECT, podendo começar com WITH.', 0, Math.min(source.length, 80), 0, true);
    const forbidden = /\b(INSERT|UPDATE|DELETE|MERGE|DROP|ALTER|CREATE|TRUNCATE|GRANT|REVOKE|CALL|EXEC|INTO)\b/gi;
    for (const m of sql.matchAll(forbidden)) add('scope', 'Instrução fora do escopo', 'Esta versão avalia consultas de leitura SELECT. Confira esta instrução.', m.index, m.index + m[0].length, 0, true);
    const semicolon = sql.indexOf(';');
    if (semicolon !== -1 && sql.slice(semicolon + 1).trim())
      add('scope', 'Mais de uma instrução', 'Analise uma consulta por vez.', semicolon, semicolon + 1, 0, true);
    const rule = (id, regex, title, message, penalty) => {
      for (const m of sql.matchAll(regex)) add(id, title, message, m.index, m.index + m[0].length, penalty);
    };
    // Procura projeções SELECT em cada nível, sem confundir COUNT(*) ou multiplicação.
    const tokens = [...sql.matchAll(/\bSELECT\b|\bFROM\b|\bDISTINCT\b|\bALL\b|\bAS\b|[A-Za-z_][\w$]*|[(),.*]|\S/g)];
    let depth = 0; const projections = new Map();
    tokens.forEach((token, n) => {
      const value = token[0].toUpperCase();
      if (value === '(') { depth++; return; }
      if (value === ')') { projections.delete(depth); depth--; return; }
      if (value === 'SELECT') projections.set(depth, n);
      if (value === 'FROM') projections.delete(depth);
      if (value === '*' && projections.has(depth)) {
        const prev = tokens[n - 1]?.[0].toUpperCase();
        if (['SELECT', 'DISTINCT', 'ALL', ',', '.'].includes(prev))
          add('star', 'Seleção de todas as colunas', 'Liste as colunas necessárias. SELECT * pode trazer campos desnecessários e variar com a estrutura da tabela.', token.index, token.index + 1, 10);
      }
    });
    rule('null', /(?:<>|!=|=)\s*NULL\b|\bNULL\s*(?:<>|!=|=)/gi, 'Comparação com NULL', 'Use IS NULL ou IS NOT NULL para verificar valores nulos.', 25);
    rule('cross', /\bCROSS\s+JOIN\b/gi, 'Multiplicação de registros', 'CROSS JOIN combina todas as linhas das duas entradas. Confirme se isso é intencional.', 15);
    rule('distinct', /\bSELECT\s+DISTINCT\b/gi, 'Remoção de duplicados', 'Confirme a necessidade de DISTINCT e verifique se joins estão gerando duplicatas.', 5);
    rule('union', /\bUNION\b(?!\s+ALL\b)/gi, 'UNION remove duplicados', 'Considere UNION ALL somente se as repetições puderem ser preservadas.', 5);
    rule('order', /\bORDER\s+BY\b/gi, 'Revisar necessidade da ordenação', 'Mantenha a ordenação quando ela for necessária ao resultado ou ao limite de linhas.', 5);
    for (const m of sql.matchAll(/\b(YEAR|MONTH|DAY|DATE|CAST|CONVERT|LOWER|UPPER|TRIM|COALESCE|SUBSTRING)\s*\(\s*[A-Za-z_][\w$]*(?:\s*\.\s*[A-Za-z_][\w$]*)*/gi)) {
      const prefix = sql.slice(0, m.index);
      const clauses = [...prefix.matchAll(/\b(WHERE|SELECT|FROM|GROUP\s+BY|ORDER\s+BY|HAVING|LIMIT|UNION)\b/gi)];
      if (clauses.at(-1)?.[0].toUpperCase() === 'WHERE')
        add('filter', 'Função em coluna de filtro', 'Pode dificultar otimizações dependendo do banco. Confira se é possível filtrar diretamente pela coluna.', m.index, m.index + m[0].length, 10);
    }
    literals.forEach(literal => {
      const before = sql.slice(0, literal.start);
      const m = /\b(?:I?LIKE)\s*$/i.exec(before);
      if (m && literal.value.startsWith('%')) add('like', 'Busca com curinga inicial', 'Uma busca por trecho pode exigir mais trabalho. Confirme se o curinga inicial é necessário.', m.index, literal.end, 10);
    });
    issues.sort((a, b) => a.start - b.start);
    const blocked = issues.some(issue => issue.blocking);
    const penalties = new Map(issues.map(issue => [issue.id, issue.penalty]));
    const score = blocked ? null : Math.max(0, 100 - [...penalties.values()].reduce((a, b) => a + b, 0));
    return { score, blocked, issues, deductions: [...penalties.values()].reduce((a, b) => a + b, 0) };
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = { analyzeSql };
  if (typeof document === 'undefined') return;
  const form = document.getElementById('sqlForm'), input = document.getElementById('sqlInput');
  const results = document.getElementById('sqlResults'), status = document.getElementById('sqlStatus');
  let latest = null;
  const escape = value => String(value).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  form.addEventListener('submit', event => {
    event.preventDefault();
    if (!input.value.trim()) { status.textContent = 'Cole uma consulta para analisar.'; return; }
    if (input.value.length > 200000) { status.textContent = 'Use até 200.000 caracteres por análise.'; return; }
    latest = analyzeSql(input.value);
    const label = latest.blocked ? 'Revise a estrutura da consulta' : latest.issues.length ? 'Consulta com pontos para revisão' : 'Nenhum alerta pelas regras atuais';
    results.innerHTML = `<div class="score ${latest.blocked ? 'bad' : latest.issues.length ? '' : 'ok'}"><div class="sql-score-number">${latest.blocked ? 'Sem nota' : latest.score + '<span>/100</span>'}</div><strong>${label}</strong><p>${latest.issues.length} ocorrência(s). A nota mede as regras em Ajuda; não garante correção ou desempenho.</p></div>` + latest.issues.map((issue, n) => `<article class="panel sql-issue"><div class="sql-issue-head"><span class="tag ${issue.blocking ? 'bad' : ''}">${issue.blocking ? 'Estrutura / escopo' : 'Revisar · −' + issue.penalty + ' pontos por regra'}</span><button type="button" class="ghost" data-issue="${n}">Ver no código · linha ${issue.line}</button></div><h3>${escape(issue.title)}</h3><pre>${escape(issue.snippet)}</pre><p>${escape(issue.message)}</p></article>`).join('');
    results.classList.remove('hidden');
    status.textContent = 'Análise concluída. O SQL não foi executado nem enviado a um serviço de IA.';
  });
  input.addEventListener('input', () => {
    latest = null; results.classList.add('hidden');
    status.textContent = input.value ? 'Consulta alterada. Clique em Analisar para atualizar o resultado.' : '';
  });
  results.addEventListener('click', event => {
    const button = event.target.closest('[data-issue]');
    if (!button || !latest) return;
    const issue = latest.issues[Number(button.dataset.issue)];
    input.focus(); input.setSelectionRange(issue.start, issue.end);
    input.scrollTop = Math.max(0, (issue.line - 3) * 23);
    input.scrollIntoView({ behavior: 'smooth', block: 'center' });
  });
})();
