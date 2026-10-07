/* Análise estática local: não executa SQL e não valida um dialeto completo. */
(function () {
  const suggestions = {
    star: { tip: 'Liste apenas as colunas usadas pelo resultado.', before: 'SELECT * FROM pedidos;', after: 'SELECT id_pedido, data_pedido, valor\nFROM pedidos;' },
    null: { tip: 'Use IS NULL para valores nulos e IS NOT NULL para valores preenchidos.', before: 'WHERE data_saida = NULL', after: 'WHERE data_saida IS NULL' },
    cross: { tip: 'Se as tabelas têm uma relação, use JOIN com a chave correta. Mantenha CROSS JOIN quando todas as combinações forem necessárias.', before: 'FROM pedidos p CROSS JOIN clientes c', after: 'FROM pedidos p\nJOIN clientes c ON c.id_cliente = p.id_cliente' },
    filter: { tip: 'Para filtro por ano, considere um intervalo direto sobre a coluna. Datas e conversões dependem do banco e do tipo da coluna; outras funções exigem uma alternativa específica.', before: 'WHERE YEAR(data_entrada) = 2026', after: "WHERE data_entrada >= '2026-01-01'\n  AND data_entrada < '2027-01-01'" },
    like: { tip: 'Se a busca puder ser por início, remova o curinga inicial. Isso muda a busca: mantenha o padrão original quando precisar encontrar o texto em qualquer posição.', before: "WHERE nome LIKE '%Maria%'", after: "-- Apenas se a busca for por início\nWHERE nome LIKE 'Maria%'" },
    distinct: { tip: 'Se o join serve apenas para verificar existência, considere EXISTS para evitar multiplicação de linhas. Não remova DISTINCT sem confirmar a equivalência; EXISTS ainda preserva duplicados já existentes na tabela principal.', before: 'SELECT DISTINCT p.id_pedido\nFROM pedidos p\nJOIN itens i ON i.id_pedido = p.id_pedido', after: '-- Se id_pedido for único em pedidos\nSELECT p.id_pedido\nFROM pedidos p\nWHERE EXISTS (\n  SELECT 1 FROM itens i\n  WHERE i.id_pedido = p.id_pedido\n)' },
    union: { tip: 'Use UNION ALL se a regra permitir preservar duplicados. Se precisar de resultados únicos, mantenha UNION.', before: 'SELECT id FROM pedidos_a\nUNION\nSELECT id FROM pedidos_b', after: 'SELECT id FROM pedidos_a\nUNION ALL\nSELECT id FROM pedidos_b' },
    order: { tip: 'Remova a ordenação apenas quando ela não for necessária. Mantenha-a para apresentação, limites determinísticos ou funções de janela que dependam da sequência.', before: 'SELECT id_pedido FROM pedidos\nORDER BY id_pedido;', after: '-- Apenas se a ordem não importar\nSELECT id_pedido FROM pedidos;' },
    tautology: { tip: 'Remova condições neutras em WHERE ou AND quando desnecessárias. Em ON ou OR, reconstrua a condição correta: apenas apagar 1 = 1 pode mudar o resultado.', before: "WHERE 1 = 1 AND status = 'ATIVO'", after: "WHERE status = 'ATIVO'" },
    notin: { tip: 'Considere NOT EXISTS com a relação correta. Defina primeiro como tratar nulos: este exemplo exclui IDs nulos da tabela principal e ignora IDs nulos da subconsulta; pode diferir do NOT IN original.', before: 'WHERE p.id_cliente NOT IN (\n  SELECT b.id_cliente FROM bloqueados b\n)', after: 'WHERE p.id_cliente IS NOT NULL\n  AND NOT EXISTS (\n    SELECT 1 FROM bloqueados b\n    WHERE b.id_cliente = p.id_cliente\n  )' },
    nullin: { tip: 'Use IS NULL explicitamente quando quiser incluir nulos. Para NOT IN, defina separadamente se os nulos devem ser incluídos ou excluídos.', before: "WHERE status IN ('ATIVO', NULL)", after: "WHERE (status IN ('ATIVO') OR status IS NULL)" },
    random: { tip: 'Se o objetivo for uma amostra, confira a sintaxe de amostragem do banco. TABLESAMPLE depende do banco, pode retornar uma quantidade variável e não equivale a sortear exatamente N linhas.', before: 'SELECT id FROM pedidos\nORDER BY RAND() LIMIT 100;', after: '-- Exemplo para bancos compatíveis; amostra percentual\nSELECT id FROM pedidos TABLESAMPLE (1 PERCENT);' },
    countdistinct: { tip: 'Só use uma contagem aproximada se a margem de erro for aceitável e o banco oferecer a função. Para validação exata, mantenha COUNT(DISTINCT).', before: 'SELECT COUNT(DISTINCT id_cliente) FROM pedidos;', after: '-- Exemplo para bancos compatíveis\nSELECT APPROX_COUNT_DISTINCT(id_cliente) FROM pedidos;' },
    window: { tip: 'Adicione PARTITION BY apenas se o cálculo deve reiniciar por grupo. O exemplo cria uma sequência por cliente; para sequência global, mantenha a janela original.', before: 'ROW_NUMBER() OVER (ORDER BY data_pedido, id_pedido)', after: 'ROW_NUMBER() OVER (\n  PARTITION BY id_cliente\n  ORDER BY data_pedido, id_pedido\n)' },
    ordinal: { tip: 'Substitua a posição pelo nome ou pela expressão correspondente no SELECT. A mesma orientação vale para GROUP BY.', before: 'SELECT id_pedido, valor FROM pedidos\nORDER BY 1;', after: 'SELECT id_pedido, valor FROM pedidos\nORDER BY id_pedido;' },
    limit: { tip: 'Defina uma ordenação estável antes do limite, incluindo um campo único como desempate. A sintaxe de LIMIT, TOP e FETCH varia por banco.', before: 'SELECT id_pedido, data_pedido FROM pedidos\nLIMIT 1000;', after: 'SELECT id_pedido, data_pedido FROM pedidos\nORDER BY data_pedido, id_pedido\nLIMIT 1000;' }
  };
  function analyzeSql(source) {
    const issues = [], literals = [];
    const chars = source.split('');
    const mask = (start, end) => { for (let j = start; j < end; j++) if (chars[j] !== '\n' && chars[j] !== '\r') chars[j] = ' '; };
    const add = (id, title, message, start, end, penalty = 0, blocking = false) => {
      issues.push({ id, title, message, start, end, penalty, blocking,
        line: source.slice(0, start).split('\n').length,
        snippet: source.slice(start, end), suggestion: suggestions[id] || null });
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
          add('star', 'Seleção de todas as colunas', 'Liste as colunas necessárias. SELECT * pode trazer campos desnecessários e variar com a estrutura da tabela.', token.index, token.index + 1, 15);
      }
    });
    rule('null', /(?:<>|!=|=)\s*NULL\b|\bNULL\s*(?:<>|!=|=)/gi, 'Comparação com NULL', 'Use IS NULL ou IS NOT NULL para verificar valores nulos.', 25);
    rule('cross', /\bCROSS\s+JOIN\b/gi, 'Multiplicação de registros', 'CROSS JOIN combina todas as linhas das duas entradas. Confirme se isso é intencional.', 20);
    rule('distinct', /\bSELECT\s+DISTINCT\b/gi, 'Remoção de duplicados', 'Confirme a necessidade de DISTINCT e verifique se joins estão gerando duplicatas.', 8);
    rule('union', /\bUNION\b(?!\s+ALL\b)/gi, 'UNION remove duplicados', 'Considere UNION ALL somente se as repetições puderem ser preservadas.', 8);
    rule('order', /\bORDER\s+BY\b/gi, 'Revisar necessidade da ordenação', 'Mantenha a ordenação quando ela for necessária ao resultado ou ao limite de linhas.', 5);
    for (const m of sql.matchAll(/\b(YEAR|MONTH|DAY|DATE|CAST|CONVERT|LOWER|UPPER|TRIM|COALESCE|SUBSTRING)\s*\(\s*[A-Za-z_][\w$]*(?:\s*\.\s*[A-Za-z_][\w$]*)*/gi)) {
      const prefix = sql.slice(0, m.index);
      const clauses = [...prefix.matchAll(/\b(WHERE|SELECT|FROM|GROUP\s+BY|ORDER\s+BY|HAVING|LIMIT|UNION)\b/gi)];
      if (clauses.at(-1)?.[0].toUpperCase() === 'WHERE')
        add('filter', 'Função em coluna de filtro', 'Pode dificultar otimizações dependendo do banco. Confira se é possível filtrar diretamente pela coluna.', m.index, m.index + m[0].length, 12);
    }
    literals.forEach(literal => {
      const before = sql.slice(0, literal.start);
      const m = /\b(?:I?LIKE)\s*$/i.exec(before);
      if (m && literal.value.startsWith('%')) add('like', 'Busca com curinga inicial', 'Uma busca por trecho pode exigir mais trabalho. Confirme se o curinga inicial é necessário.', m.index, literal.end, 12);
    });
    rule('tautology', /\b(?:WHERE|AND|OR|ON)\s+1\s*=\s*1\b/gi, 'Condição sempre verdadeira', '1 = 1 não restringe registros. Em ON ou OR pode ampliar o resultado; em SQL dinâmico pode ser intencional.', 8);
    rule('notin', /\bNOT\s+IN\s*\(\s*SELECT\b/gi, 'NOT IN com subconsulta', 'Se a subconsulta retornar NULL, o resultado pode ser inesperado. Confira os nulos e considere NOT EXISTS com uma correlação equivalente.', 15);
    rule('nullin', /\b(?:NOT\s+)?IN\s*\([^()]*\bNULL\b[^()]*\)/gi, 'NULL dentro de IN', 'NULL na lista não equivale a IS NULL. Revise a condição, especialmente com NOT IN.', 20);
    rule('random', /\bORDER\s+BY\s+(?:RAND|RANDOM|NEWID)\s*\(/gi, 'Ordenação aleatória', 'Ordenar por uma função aleatória pode aumentar bastante o trabalho. Confira se há uma alternativa de amostragem no seu banco.', 10);
    rule('countdistinct', /\bCOUNT\s*\(\s*DISTINCT\b/gi, 'Contagem distinta', 'A contagem exata de valores distintos pode custar mais em grandes volumes. Mantenha se a exatidão for necessária; aproximações mudam o resultado.', 5);
    rule('window', /\bOVER\s*\(\s*ORDER\s+BY\b/gi, 'Janela sem PARTITION BY', 'Esta janela trata toda a entrada como um grupo. Confirme se a regra exige cálculo global ou por grupo.', 8);
    // Constantes numéricas não têm significado de coluna e deixam agrupamentos frágeis.
    rule('ordinal', /\b(?:ORDER|GROUP)\s+BY\s+\d+\s*(?=,|ASC\b|DESC\b|LIMIT\b|OFFSET\b|FETCH\b|HAVING\b|;|\)|$)/gi, 'Referência por posição', 'Use o nome ou expressão da coluna. Uma mudança na ordem do SELECT pode alterar o significado de ORDER BY ou GROUP BY por posição.', 5);
    if (!/\bORDER\s+BY\b/i.test(sql)) {
      rule('limit', /\bLIMIT\s+\d+\b|\bTOP\s*(?:\(\s*\d+\s*\)|\d+)|\bFETCH\s+(?:FIRST|NEXT)\s+\d+\s+ROWS\b/gi, 'Limite sem ordenação explícita', 'A seleção das linhas pode variar sem ORDER BY. Para amostras comparáveis, use uma ordenação determinística com critério de desempate.', 10);
    }
    issues.sort((a, b) => a.start - b.start);
    const blocked = issues.some(issue => issue.blocking);
    const penalties = new Map(issues.map(issue => [issue.id, issue.penalty]));
    const score = blocked ? null : Math.max(0, 100 - [...penalties.values()].reduce((a, b) => a + b, 0));
    return { score, blocked, issues, ruleCount: penalties.size, deductions: [...penalties.values()].reduce((a, b) => a + b, 0) };
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = { analyzeSql };
  if (typeof document === 'undefined') return;
  const form = document.getElementById('sqlForm'), input = document.getElementById('sqlInput');
  const results = document.getElementById('sqlResults'), status = document.getElementById('sqlStatus');
  document.querySelectorAll('[data-help]').forEach(button => button.addEventListener('click', () => {
    const isSql = button.dataset.help === 'sql';
    document.getElementById('helpSql').classList.toggle('hidden', !isSql);
    document.getElementById('helpCsv').classList.toggle('hidden', isSql);
    document.querySelectorAll('[data-help]').forEach(item => item.setAttribute('aria-pressed', String(item === button)));
  }));
  let latest = null;
  let generation = 0;
  const aiResults = document.getElementById('sqlAiResults');
  const model = document.getElementById('sqlModel');
  form.addEventListener('reset', () => {
    const selectedModel = model.value;
    generation++; latest = null;
    aiResults.innerHTML = ''; aiResults.classList.add('hidden');
    results.innerHTML = ''; results.classList.add('hidden'); status.textContent = '';
    form.querySelector('button[type="submit"]').disabled = false;
    queueMicrotask(() => { model.value = selectedModel; input.focus(); });
  });
  const escape = value => String(value).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const renderSuggestion = suggestion => suggestion ? `<div class="sql-suggestion"><h4>Dica de melhoria</h4><p>${escape(suggestion.tip)}</p><div class="sql-examples"><div><span>Antes · exemplo</span><pre>${escape(suggestion.before)}</pre></div><div><span>Alternativa · exemplo</span><pre>${escape(suggestion.after)}</pre></div></div><small>Nomes ilustrativos. Adapte ao seu banco, às tabelas e à regra de negócio; valide os resultados antes de substituir.</small></div>` : '';
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (!input.value.trim()) { status.textContent = 'Cole uma consulta para analisar.'; return; }
    if (input.value.length > 200000) { status.textContent = 'Use até 200.000 caracteres por análise.'; return; }
    latest = analyzeSql(input.value);
    const requestGeneration = ++generation;
    const query = input.value;
    const provider = model.value;
    aiResults.classList.add('hidden'); aiResults.innerHTML = '';
    const label = latest.blocked ? 'Revise a estrutura da consulta' : latest.issues.length ? 'Consulta com pontos para revisão' : 'Nenhum alerta pelas regras atuais';
    results.innerHTML = `<div class="score ${latest.blocked ? 'bad' : latest.issues.length ? '' : 'ok'}"><div class="sql-score-number">${latest.blocked ? 'Sem nota' : latest.score + '<span>/100</span>'}</div><strong>${label}</strong><p>${latest.issues.length} ocorrência(s). A nota mede as regras em Ajuda; não garante correção ou desempenho.</p></div>` + latest.issues.map((issue, n) => `<article class="panel sql-issue"><div class="sql-issue-head"><span class="tag ${issue.blocking ? 'bad' : ''}">${issue.blocking ? 'Estrutura / escopo' : 'Revisar · −' + issue.penalty + ' pontos por regra'}</span><button type="button" class="ghost" data-issue="${n}">Ver no código · linha ${issue.line}</button></div><h3>${escape(issue.title)}</h3><pre>${escape(issue.snippet)}</pre><p>${escape(issue.message)}</p>${renderSuggestion(issue.suggestion)}</article>`).join('');
    results.classList.remove('hidden');
    status.textContent = 'Análise concluída. O SQL não foi executado nem enviado a um serviço de IA.';
    if (provider === 'off') return;
    if (latest.blocked) { status.textContent = 'Corrija a estrutura ou o escopo para continuar com IA. Nenhuma chamada foi feita.'; return; }
    const button = form.querySelector('button[type="submit"]');
    button.disabled = true;
    status.textContent = 'Regras concluídas. Analisando com ' + (provider === 'groq' ? 'Groq' : 'Gemini') + '...';
    aiResults.classList.remove('hidden');
    aiResults.innerHTML = '<div class="sql-thinking"><span></span><span></span><span></span></div><p>Avaliando sua consulta...</p>';
    try {
      const { data, error } = await authClient.auth.getSession();
      if (error || !data.session?.access_token) throw new Error('Entre novamente para usar a IA.');
      const response = await fetch('/api/analyze-sql', { method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + data.session.access_token }, body: JSON.stringify({ sql: query, provider }), signal: AbortSignal.timeout(65000) });
      let payload; try { payload = await response.json(); } catch { throw new Error('Backend indisponível. Confira o deploy da pasta api na Vercel.'); }
      if (!response.ok) throw new Error(payload.error || 'Não foi possível analisar com IA.');
      if (generation !== requestGeneration || !activeEmail) return;
      const analysis = payload.analysis;
      aiResults.innerHTML = `<span class="tag">${provider === 'groq' ? 'Groq · GPT-OSS 120B' : 'Gemini · Flash Lite'}</span><h3 class="sql-ai-title">Análise complementar</h3><p>${escape(analysis.summary)}</p>` + analysis.findings.map(f => `<div class="sql-ai-finding"><span>${escape(f.severity)} · ${f.line ? 'Linha ' + f.line : 'Linha não confirmada'}</span><h4>${escape(f.title)}</h4><pre>${escape(f.snippet)}</pre><p>${escape(f.explanation)}</p><h4>Dica de melhoria</h4><pre>${escape(f.suggestion)}</pre></div>`).join('') + `<p class="sql-disclosure">${escape(analysis.limitations || 'Valide as sugestões e compare os resultados antes de substituir a consulta.')}</p><p class="sql-disclosure">Você ainda tem ${Number(payload.userRemaining)} análises neste fornecedor na janela de 24 horas. A cota compartilhada pode terminar antes. A IA pode errar; sua resposta não altera a nota automática.</p>`;
      status.textContent = 'Análise automática e complementar concluídas.';
    } catch (error) {
      if (generation !== requestGeneration || !activeEmail) return;
      aiResults.innerHTML = '';
      const p = document.createElement('p');
      p.textContent = error.name === 'TimeoutError' ? 'A análise demorou além do limite. A tentativa pode contar na cota.' : error.message;
      aiResults.appendChild(p);
      status.textContent = 'A análise por regras continua disponível.';
    } finally { if (generation === requestGeneration) button.disabled = false; }
  });
  input.addEventListener('input', () => {
    generation++;
    aiResults.classList.add('hidden');
    latest = null; results.classList.add('hidden');
    status.textContent = input.value ? 'Consulta alterada. Clique em Enviar para atualizar o resultado.' : '';
  });
  model.addEventListener('change', () => { generation++; aiResults.classList.add('hidden'); status.textContent = 'Modelo alterado. Envie a consulta para analisar com a opção selecionada.'; });
  results.addEventListener('click', event => {
    const button = event.target.closest('[data-issue]');
    if (!button || !latest) return;
    const issue = latest.issues[Number(button.dataset.issue)];
    input.focus(); input.setSelectionRange(issue.start, issue.end);
    input.scrollTop = Math.max(0, (issue.line - 3) * 23);
    input.scrollIntoView({ behavior: 'smooth', block: 'center' });
  });
})();
