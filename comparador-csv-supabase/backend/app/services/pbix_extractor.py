"""Read-only PBIX inventory. Findings are advisory, never deletion instructions."""
import json
import re
import zipfile
from collections import defaultdict, deque

from .dax_dependencies import references
from .model_metadata import EmbeddedMetadata, model_inventory, model_dependencies

MAX_LAYOUT = 24 * 1024 * 1024


def records(frame):
    return json.loads(frame.to_json(orient='records'))


def walk(value, path='Relatório', depth=0):
    if depth > 80:
        raise ValueError('Estrutura do relatório excede a profundidade permitida.')
    if isinstance(value, str):
        text = value.strip()
        if text.startswith(('{', '[')):
            try:
                yield from walk(json.loads(text), path, depth + 1)
                return
            except json.JSONDecodeError:
                pass
        yield path, value
    elif isinstance(value, dict):
        yield path, value
        for key, child in value.items():
            yield from walk(child, f'{path}/{key}', depth + 1)
    elif isinstance(value, list):
        for i, child in enumerate(value):
            yield from walk(child, f'{path}/{i + 1}', depth + 1)


def read_layout(path):
    """Read classic Layout, legacy report.json and PBIR JSON parts inside PBIX."""
    def decode(blob):
        for encoding in ('utf-8-sig', 'utf-16', 'utf-16-le'):
            try:
                value = json.loads(blob.decode(encoding))
                if isinstance(value, dict):
                    return value
            except (UnicodeError, json.JSONDecodeError):
                continue
        raise ValueError('Parte do relatório não pôde ser interpretada como JSON.')
    with zipfile.ZipFile(path) as archive:
        members = archive.infolist()
        classic = [x for x in members if x.filename.replace('\\', '/').lower() in ('report/layout', 'report/report.json')]
        if len(classic) > 1:
            raise ValueError('Mais de uma definição principal de relatório encontrada.')
        if classic:
            if classic[0].file_size > MAX_LAYOUT:
                raise ValueError('Layout do relatório excede o limite de 24 MB.')
            value = decode(archive.read(classic[0]))
            if not isinstance(value.get('sections'), list):
                raise ValueError('Estrutura de páginas não reconhecida.')
            return value
        parts = [x for x in members if x.filename.replace('\\', '/').lower().startswith('report/definition/') and x.filename.lower().endswith('.json')]
        if not parts or len(parts) > 5000 or sum(x.file_size for x in parts) > MAX_LAYOUT:
            raise ValueError('Definição do relatório ausente, não suportada ou acima do limite.')
        names = {x.filename.replace('\\', '/').lower() for x in parts}
        if 'report/definition/report.json' not in names or 'report/definition/pages/pages.json' not in names:
            raise ValueError('Definição PBIR incompleta: faltam o relatório ou o índice de páginas.')
        payload = {x.filename: decode(archive.read(x)) for x in parts}
        pages = [v for k, v in payload.items() if k.replace('\\', '/').lower().endswith('/page.json')]
        manifest = next(v for k,v in payload.items() if k.replace('\\', '/').lower() == 'report/definition/pages/pages.json')
        order = manifest.get('pageOrder')
        if isinstance(order, list):
            for page in order:
                expected = 'report/definition/pages/' + str(page).lower() + '/page.json'
                if expected not in names:
                    raise ValueError('Definição PBIR incompleta: página listada não foi encontrada.')
        return {'sections': pages, 'pbir': payload}


def dax_refs(expression):
    # Ignore strings/comments, preserving quoted table names and bracket identifiers.
    token = re.compile(r'"(?:""|[^"])*"|//[^\n]*|--[^\n]*|/\*.*?\*/|\[(?:\]\]|[^\]])+\]', re.S)
    refs = []
    for match in token.finditer(expression or ''):
        value = match.group()
        if value.startswith('['):
            refs.append(value[1:-1].replace(']]', ']'))
    return refs


def classify(measures, columns, expressions, relationships, layout, complete, warnings, structural=None):
    items = []
    for kind, source in [('Medida', measures), ('Coluna', columns)]:
        for row in source:
            name = row.get('Name') or row.get('ColumnName')
            table = row.get('TableName')
            if name and table:
                items.append({'type': kind, 'table': str(table), 'name': str(name), 'evidence': []})
    key = lambda table, name, kind: (str(table).casefold(), str(name).casefold(), kind)
    index_by_key = {key(i['table'], i['name'], i['type']): n for n, i in enumerate(items)}
    by_name, by_table, by_query_ref = defaultdict(list), defaultdict(list), defaultdict(list)
    for n, item in enumerate(items):
        by_name[item['name'].casefold()].append(n)
        by_table[item['table'].casefold()].append(n)
        by_query_ref[(item['table'] + '.' + item['name']).casefold()].append(n)
    known_tables = list(dict.fromkeys(i['table'] for i in items))
    roots, edges, incoming = set(), defaultdict(set), defaultdict(set)
    root_reasons, uncertain = {}, defaultdict(list)
    global_complete = complete
    global_reasons = list(warnings) if not complete else []
    structural = structural or {'complete': False}

    def block(reason):
        nonlocal global_complete
        global_complete = False
        global_reasons.append(reason)
        warnings.append(reason)

    def evidence(n, reason):
        if reason not in items[n]['evidence'] and len(items[n]['evidence']) < 10:
            items[n]['evidence'].append(reason)

    def doubt(ids, reason):
        for n in ids:
            if reason not in uncertain[n]:
                uncertain[n].append(reason)
        if reason not in warnings:
            warnings.append(reason)

    def candidates(name, table=None, kind=None):
        return [n for n in by_name.get(str(name).casefold(), [])
                if (not table or items[n]['table'].casefold() == str(table).casefold())
                and (not kind or items[n]['type'] == kind)]

    def mark(name, reason, table=None, kind=None):
        targets = candidates(name, table, kind)
        if len(targets) > 1:
            doubt(targets, f'Referência ambígua a [{name}] em {reason}; falta identificar a tabela.')
        elif targets:
            n = targets[0]
            roots.add(n)
            root_reasons.setdefault(n, reason)
            evidence(n, reason)
        return bool(targets)

    def link(source, target, reason):
        edges[source].add(target)
        incoming[target].add(source)
        evidence(target, reason)

    for entry in expressions:
        label, expression = entry[:2]
        owner = entry[2] if len(entry) > 2 else None
        sources = [index_by_key[key(*owner)]] if owner and key(*owner) in index_by_key else []
        if len(entry) == 2:
            sources = candidates(label, kind='Medida')
        refs, whole_tables, virtual, problems = references(expression, known_tables)
        for problem in problems:
            reason = problem + ' Em ' + label
            block(reason)
        if problems:
            # Invalid lexical input can hide arbitrary references, so its
            # apparent tokens must not prove either use or non-use.
            continue
        for table, name in refs:
            targets = candidates(name, table)
            # Local virtual columns do not exist in the semantic inventory.
            if not targets and not table and name.casefold() in virtual:
                continue
            if not targets:
                reason = f'Referência DAX não resolvida: {table or ""}[{name}] em {label}.'
                if sources:
                    doubt(sources, reason)
                else:
                    block(reason)
                continue
            if len(targets) > 1 or (not table and name.casefold() in virtual):
                doubt(targets, f'Referência DAX ambígua a [{name}] em {label}.')
                # Possible references are not confirmed edges. Doubt is propagated
                # separately so a possible match never turns green.
                for target in targets:
                    incoming[target].update(sources)
                continue
            target = targets[0]
            reason = 'Dependência em ' + label
            if sources:
                for source in sources:
                    link(source, target, reason)
            else:
                roots.add(target)
                root_reasons.setdefault(target, reason)
                evidence(target, reason)
        for table in whole_tables:
            for target in by_table[table.casefold()]:
                if items[target]['type'] != 'Coluna':
                    continue
                reason = 'Tabela inteira referenciada em ' + label
                if sources:
                    for source in sources:
                        link(source, target, reason)
                else:
                    roots.add(target)
                    root_reasons.setdefault(target, reason)
                    evidence(target, reason)

    for relation in relationships:
        for side in ('From', 'To'):
            if relation.get(side + 'ColumnName') and not mark(relation[side + 'ColumnName'], 'Participa de relacionamento', relation.get(side + 'TableName'), 'Coluna'):
                structural['complete'] = False
                warnings.append('Coluna de relacionamento não encontrada no inventário.')
    for table, name, reason in structural.get('roots', []):
        if not mark(name, reason, table, 'Coluna'):
            structural['complete'] = False
            warnings.append('Dependência estrutural sem correspondência no inventário.')
    for table, name, reason in structural.get('measure_roots', []):
        if not mark(name, reason, table, 'Medida'):
            block('Medida estrutural sem correspondência no inventário.')
    for owner, target, reason in structural.get('edges', []):
        source_id, target_id = index_by_key.get(key(*owner)), index_by_key.get(key(*target))
        if source_id is None or target_id is None:
            structural['complete'] = False
            warnings.append('Dependência estrutural sem correspondência no inventário.')
        else:
            link(source_id, target_id, reason + f' de {owner[0]}[{owner[1]}]')

    if layout is not None:
        aliases = defaultdict(lambda: defaultdict(set))
        for path, node in walk(layout):
            if isinstance(node, dict) and node.get('Name') and node.get('Entity'):
                scope = path.split('/From/')[0] if '/From/' in path else path.rsplit('/', 1)[0]
                aliases[scope][str(node['Name'])].add(str(node['Entity']))

        def entity(source_ref, path):
            if source_ref.get('Entity'):
                return {source_ref['Entity']}
            alias = source_ref.get('Source')
            if alias:
                scopes = [s for s in aliases if path == s or path.startswith(s + '/')]
                for scope in sorted(scopes, key=len, reverse=True):
                    if alias in aliases[scope]:
                        return aliases[scope][alias]
                # Cross-part queries sometimes carry the alias in another JSON.
                return set().union(*(a.get(alias, set()) for a in aliases.values()))
            return set()

        def report_ref(name, source_ref, kind, path):
            tables = entity(source_ref, path)
            reason = 'Referência no relatório: ' + path
            if source_ref.get('Source') and not tables:
                targets = candidates(name, kind=kind)
                doubt(targets, f'Alias de tabela não resolvido para [{name}] em {path}.')
                if not targets:
                    block('Alias sem correspondência no inventário em ' + path)
                return
            if len(tables) > 1:
                targets = [n for n in candidates(name, kind=kind) if items[n]['table'] in tables]
                doubt(targets, f'Alias de tabela ambíguo para [{name}] em {path}.')
                if not targets:
                    block('Alias sem correspondência no inventário em ' + path)
                return
            if not mark(name, reason, next(iter(tables), None), kind):
                block('Referência do relatório não encontrada: ' + str(name) + ' em ' + path)

        for path, node in walk(layout):
            if isinstance(node, dict):
                for field, kind in [('Measure', 'Medida'), ('Column', 'Coluna'), ('PropertyVariationSource', 'Coluna')]:
                    ref = node.get(field)
                    if isinstance(ref, dict) and ref.get('Property'):
                        source_ref = ref.get('Expression', {}).get('SourceRef', {})
                        report_ref(ref['Property'], source_ref, kind, path)
                level = node.get('HierarchyLevel')
                if isinstance(level, dict):
                    hierarchy = level.get('Expression', {}).get('Hierarchy', {})
                    tables = entity(hierarchy.get('Expression', {}).get('SourceRef', {}), path)
                    matched = [h for h in structural.get('hierarchies', [])
                               if h.get('name') == hierarchy.get('Hierarchy')
                               and (not tables or h.get('table') in tables)
                               and h.get('level') == level.get('Level')]
                    if matched:
                        for h in matched:
                            mark(h['column'][1], 'Nível de hierarquia no relatório: ' + path, h['column'][0], 'Coluna')
                    else:
                        block('Nível de hierarquia do relatório não resolvido em ' + path)
                visual = node.get('visualType')
                if visual and str(visual).casefold() not in {
                    'tableex','pivottable','card','cardvisual','multirowcard','slicer',
                    'barchart','columnchart','clusteredbarchart','clusteredcolumnchart','stackedbarchart',
                    'stackedcolumnchart','hundredpercentstackedbarchart','hundredpercentstackedcolumnchart',
                    'linechart','areachart','stackedareachart','piechart','donutchart','scatterchart',
                    'waterfallchart','treemap','gauge','kpi','map','filledmap','textbox','image','shape',
                    'actionbutton','lineclusteredcolumncombochart','linestackedcolumncombochart',
                    'advancedslicervisual','azuremap','funnel','ribbonchart','decompositiontreevisual','keydriversvisual'}:
                    block('Visual não coberto nesta versão: ' + str(visual) + '. Itens sem uso ficam para revisão.')
            elif isinstance(node, str) and path.rsplit('/', 1)[-1].casefold() in ('queryref', 'queryname', 'field'):
                # Exact semantic names only: titles and substring matches do not
                # prove usage (T.A must not match T.Antiga).
                value = node.strip()
                wrapped = re.fullmatch(r'[\w]+\((.*)\)', value)
                if wrapped:
                    value = wrapped[1]
                targets = by_query_ref.get(value.casefold(), [])
                if len(targets) > 1:
                    doubt(targets, 'Tipo de objeto ambíguo no campo da consulta: ' + path)
                elif targets:
                    item = items[targets[0]]
                    mark(item['name'], 'Campo da consulta do visual: ' + path, item['table'], item['type'])

    reachable = set(roots)
    parents, origins = {}, {n: n for n in roots}
    pending = deque(sorted(roots))
    while pending:
        source = pending.popleft()
        for target in sorted(edges[source]):
            if target not in reachable:
                reachable.add(target)
                parents[target], origins[target] = source, origins[source]
                pending.append(target)
    # An ambiguous reference only affects the possible targets and their known
    # dependency chains. Missing layout/model still blocks negatives globally.
    pending = deque(sorted(uncertain))
    while pending:
        source = pending.popleft()
        for target in sorted(edges[source]):
            if target not in uncertain:
                uncertain[target] = ['Depende de uma referência que não foi confirmada: ' + items[source]['name']]
                pending.append(target)

    def label(n):
        return items[n]['table'] + '[' + items[n]['name'] + ']'

    for index, item in enumerate(items):
        item['review_reasons'] = []
        item['usage_path'] = []
        item['used_by'] = [label(n) for n in sorted(incoming[index])[:10]]
        if index in reachable:
            item['status'] = 'used'
            item['reason'] = ('Uso encontrado no relatório ou em uma dependência estrutural.' if index in roots else
                              'Necessário para um item com uso encontrado, através da cadeia de dependências.')
            path, cursor = [], index
            for _ in range(16):
                path.append(label(cursor))
                if cursor not in parents:
                    break
                cursor = parents[cursor]
            root = origins[index]
            prefix = [root_reasons[root]]
            if cursor in parents:
                prefix += [label(root), '… dependências intermediárias …']
            item['usage_path'] = prefix + list(reversed(path))
        elif not global_complete or index in uncertain or (item['type'] == 'Coluna' and not structural.get('complete')):
            item['status'] = 'review'
            reasons = list(uncertain.get(index, []))
            if not global_complete:
                reasons += global_reasons[:4] or ['Leitura do modelo ou relatório incompleta.']
            if item['type'] == 'Coluna' and not structural.get('complete'):
                reasons.append('Não foi possível confirmar todas as dependências estruturais de colunas.')
            item['review_reasons'] = list(dict.fromkeys(reasons))[:8]
            item['reason'] = 'Analisar: ' + item['review_reasons'][0]
        elif incoming[index]:
            item['status'] = 'unused_dependency'
            item['reason'] = 'Referenciado apenas por itens que não chegam a um uso encontrado no relatório analisado.'
        else:
            item['status'] = 'unused'
            item['reason'] = 'Nenhum uso ou referência de outro cálculo encontrado no escopo analisado. Valide usos externos antes de remover.'
    return items, global_complete and not uncertain and not any(i['status'] == 'review' for i in items), list(dict.fromkeys(warnings))


def extract_pbix(path, filename):
    warnings = []
    complete = True
    layout = None
    try:
        layout = read_layout(path)
    except Exception as error:
        warnings.append(str(error))
        complete = False
    measures, columns, expressions, relationships, tables = [], [], [], [], []
    structural = {'roots': [], 'complete': False}
    model = None
    try:
        from pbixray import PBIXRay
        model = PBIXRay(path, on_disk=True)
        try:
            metadata = EmbeddedMetadata(model)
            tables, measures, columns, relationships, names, column_ids, measure_ids = model_inventory(metadata)
            expressions, structural, dependencies_complete, dependency_warnings = model_dependencies(metadata, names, column_ids, measure_ids)
            complete = complete and dependencies_complete
            warnings.extend(dependency_warnings)
        except Exception:
            # Keep positive findings if the strict metadata reader cannot handle
            # a model. Public views are not sufficient to prove absence of use.
            complete = False
            structural = {'roots': [], 'complete': False}
            warnings.append('Leitura lógica e estrutural do modelo incompleta; inventário alternativo não permite confirmar ausência de uso.')
            if not measures and not columns:
                tables = [str(x) for x in model.tables]
                measures = records(model.dax_measures)
                columns = records(model.schema)
                relationships = records(model.relationships)
            expressions = []
            for attr in ('dax_measures', 'dax_columns', 'dax_tables', 'rls'):
                for row in records(getattr(model, attr)):
                    for field, value in row.items():
                        if isinstance(value, str) and ('expression' in field.casefold() or field.casefold() == 'filter'):
                            owner = (row.get('TableName'), row.get('Name') or row.get('ColumnName'),
                                     'Medida' if attr == 'dax_measures' else 'Coluna') if attr in ('dax_measures', 'dax_columns') else None
                            expressions.append((attr + ': ' + str(row.get('Name', '')), value, owner))
    except Exception:
        complete = False
        warnings.append('Modelo não extraído integralmente. PBIX protegido, conexão remota ou formato incompatível podem exigir outra abordagem.')
    finally:
        if model is not None:
            model.close()
    if len(tables) > 2000 or len(measures) + len(columns) > 20000:
        raise ValueError('Modelo excede o limite de 2.000 tabelas ou 20.000 medidas e colunas.')
    items, complete, warnings = classify(measures, columns, expressions, relationships, layout, complete, warnings, structural)
    if not tables and not items:
        raise ValueError('Não foi possível extrair o modelo deste PBIX. Relatórios com modelo remoto não contêm todo o inventário necessário.')
    summary = {'tables': len(tables), 'measures': sum(i['type'] == 'Medida' for i in items),
               'columns': sum(i['type'] == 'Coluna' for i in items), 'pages': len(layout['sections']) if layout else None,
               'used': sum(i['status'] == 'used' for i in items),
               'unused': sum(i['status'] == 'unused' for i in items),
               'unused_dependency': sum(i['status'] == 'unused_dependency' for i in items),
               'review': sum(i['status'] == 'review' for i in items)}
    return {'filename': filename, 'analysis_version': 14, 'summary': summary, 'items': items, 'warnings': warnings,
            'coverage': 'limited' if not complete else 'expanded',
            'limitations': 'Análise estática conservadora do arquivo enviado, sem executar DAX. Referências de outros relatórios, Excel, modelos compartilhados e usos externos não são verificadas. Colunas sem uso só são classificadas quando a leitura estrutural é confirmada; casos não cobertos permanecem em Analisar. Não modifica nem exclui itens do PBIX.'}
