"""Read-only PBIX inventory. Findings are advisory, never deletion instructions."""
import json
import re
import zipfile
from collections import defaultdict

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
    index_by_key = {(i['table'].casefold(), i['name'].casefold(), i['type']): n for n, i in enumerate(items)}
    by_name = defaultdict(list)
    for item in items:
        by_name[item['name'].casefold()].append(item)

    roots = set()
    edges = defaultdict(set)
    incoming = defaultdict(set)
    def mark(name, reason, table=None, kind=None):
        found = False
        for item in by_name.get(str(name).casefold(), []):
            if table and item['table'].casefold() != str(table).casefold():
                continue
            if kind and item['type'] != kind:
                continue
            if reason not in item['evidence']:
                item['evidence'].append(reason)
            roots.add(index_by_key[(item['table'].casefold(), item['name'].casefold(), item['type'])])
            found = True
        return found

    for entry in expressions:
        label, expression = entry[:2]
        owner = entry[2] if len(entry) > 2 else None
        sources = [i for i, item in enumerate(items) if owner and
                   item['table'] == owner[0] and item['name'] == owner[1] and item['type'] == owner[2]]
        # Compatibility for fixtures/legacy callers using a measure name as label.
        if len(entry) == 2:
            sources = [index_by_key[(item['table'].casefold(), item['name'].casefold(), item['type'])] for item in by_name.get(label.casefold(), []) if item['type'] == 'Medida']
        cleaned = re.sub(r'"(?:""|[^"])*"|//[^\n]*|--[^\n]*|/\*.*?\*/', lambda m: ' ' * len(m.group()), expression or '', flags=re.S)
        qualified = {}
        for match in re.finditer(r"(?:'((?:''|[^'])+)'|([\w]+))\s*\[((?:\]\]|[^\]])+)\]", cleaned):
            name = match[3].replace(']]', ']')
            qualified.setdefault(name.casefold(), set()).add((match[1] or match[2]).replace("''", "'").casefold())
        bracket_names = dax_refs(expression)
        for name in list(qualified):
            occurrences = sum(n.casefold() == name for n in bracket_names)
            qualified_occurrences = sum(m[3].replace(']]', ']').casefold() == name for m in re.finditer(r"(?:'((?:''|[^'])+)'|([\w]+))\s*\[((?:\]\]|[^\]])+)\]", cleaned))
            if occurrences > qualified_occurrences:
                del qualified[name]
        refs = [(name, by_name.get(name.casefold(), []), False) for name in bracket_names]
        for table in {i['table'] for i in items}:
            escaped = table.replace("'", "''")
            # Table used as an argument (COUNTROWS/FILTER/ALL/VALUES/etc.) or table variable source.
            pattern = r"(?<![\w'])" + (re.escape("'" + escaped + "'") + (r'|' + re.escape(table) + r'(?![\w])' if re.fullmatch(r'\w+', table) else ''))
            if re.search(r'(?:^|[,(=])\s*(?:' + pattern + r')\s*(?=[,)]|$)', cleaned):
                refs.extend((i['name'], [i], True) for i in items if i['type'] == 'Coluna' and i['table'] == table)
        for name, targets, whole_table in refs:
            if not whole_table and name.casefold() in qualified:
                targets = [t for t in targets if t['table'].casefold() in qualified[name.casefold()]]
            if not targets:
                complete = False
                warnings.append('Uma expressão contém referências não resolvidas; itens sem uso precisam de análise.')
            for target in targets:
                target_id = index_by_key[(target['table'].casefold(), target['name'].casefold(), target['type'])]
                reason = 'Dependência em ' + label
                if reason not in target['evidence']:
                    target['evidence'].append(reason)
                if sources:
                    for source in sources:
                        edges[source].add(target_id)
                        incoming[target_id].add(source)
                else:
                    # Security / table expressions without an inventory owner are roots.
                    roots.add(target_id)
    for relation in relationships:
        for side in ('From', 'To'):
            mark(relation.get(side + 'ColumnName', ''), 'Participa de relacionamento', relation.get(side + 'TableName'), 'Coluna')

    if structural:
        for table, name, reason in structural.get('roots', []):
            if not mark(name, reason, table, 'Coluna'):
                complete = False
                warnings.append('Dependência estrutural sem correspondência no inventário.')
    if layout is not None:
        aliases = defaultdict(set)
        for _, node in walk(layout):
            if isinstance(node, dict) and node.get('Name') and node.get('Entity'):
                aliases[str(node['Name'])].add(str(node['Entity']))
        for path, node in walk(layout):
            if isinstance(node, dict):
                for field, kind in [('Measure', 'Medida'), ('Column', 'Coluna')]:
                    ref = node.get(field)
                    if isinstance(ref, dict) and ref.get('Property'):
                        source_ref = ref.get('Expression', {}).get('SourceRef', {})
                        tables = {source_ref['Entity']} if source_ref.get('Entity') else aliases.get(source_ref.get('Source'), set())
                        found = False
                        for table in tables or {None}:
                            found = mark(ref['Property'], 'Referência no relatório: ' + path, table, kind) or found
                        if not found:
                            complete = False
                            warnings.append('O relatório contém referências sem correspondência no modelo extraído.')
                visual = node.get('visualType')
                if visual and str(visual).casefold() not in {
                    'tableex','pivotTable'.casefold(),'card','cardvisual','multirowcard','slicer',
                    'barchart','columnchart','clusteredbarchart','clusteredcolumnchart','stackedbarchart',
                    'stackedcolumnchart','hundredpercentstackedbarchart','hundredpercentstackedcolumnchart',
                    'linechart','areachart','stackedareachart','piechart','donutchart','scatterchart',
                    'waterfallchart','treemap','gauge','kpi','map','filledmap','textbox','image','shape',
                    'actionbutton','lineclusteredcolumncombochart','linestackedcolumncombochart',
                    'advancedSlicerVisual'.casefold(),'azuremap','funnel','ribbonchart','decompositiontreevisual','keydriversvisual'}:
                    complete = False
                    warnings.append('Visual não coberto nesta versão: ' + str(visual) + '. Itens sem uso ficam para revisão.')
            elif isinstance(node, str):
                for name in dax_refs(node):
                    mark(name, 'Referência textual no relatório: ' + path)
                # queryRef convention Table.Measure; false positives retain items.
                for item in items:
                    if (item['table'] + '.' + item['name']).casefold() in node.casefold():
                        mark(item['name'], 'Referência textual no relatório: ' + path, item['table'], item['type'])

    reachable = set(roots)
    pending = list(roots)
    while pending:
        source = pending.pop()
        for target in edges[source]:
            if target not in reachable:
                reachable.add(target)
                pending.append(target)
    for index, item in enumerate(items):
        if index in reachable:
            item['status'] = 'used'
            item['reason'] = ('Uso encontrado no relatório ou em uma dependência estrutural.' if index in roots else
                              'Necessário para um item com uso encontrado, através da cadeia de dependências.')
        elif not complete or (item['type'] == 'Coluna' and not (structural and structural.get('complete'))):
            item['status'] = 'review'
            item['reason'] = ('Analisar: não foi possível confirmar o uso devido às limitações da leitura.' if not complete else
                              'Analisar: usos estruturais e cálculos sobre a tabela inteira exigem revisão.')
        elif incoming[index]:
            item['status'] = 'unused_dependency'
            item['reason'] = 'Referenciado apenas por cálculos que não chegam a um uso encontrado no relatório analisado.'
        else:
            item['status'] = 'unused'
            item['reason'] = 'Nenhum uso ou referência de outro cálculo encontrado no escopo analisado. Valide usos externos antes de remover.'
        item['evidence'] = item['evidence'][:10]
    return items, complete, list(dict.fromkeys(warnings))


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
        tables = [str(x) for x in model.tables]
        if len(tables) > 2000:
            raise ValueError('Modelo excede 2.000 tabelas.')
        measures = records(model.dax_measures)
        columns = records(model.schema)
        relationships = records(model.relationships)
        if len(measures) + len(columns) > 20000:
            raise ValueError('Modelo excede 20.000 medidas e colunas.')
        for attr in ('dax_measures', 'dax_columns', 'dax_tables', 'rls',
                     'tmschema_calculation_items', 'tmschema_format_string_definitions',
                     'tmschema_detail_rows_definitions', 'tmschema_kpis',
                     'tmschema_calculation_expressions', 'tmschema_functions'):
            try:
                rows = records(getattr(model, attr))
                for row in rows:
                    for key, value in row.items():
                        if isinstance(value, str) and ('expression' in key.casefold() or key.casefold() in ('filter', 'kpi', 'definition')):
                            owner = (row.get('TableName'), row.get('Name') or row.get('ColumnName'), 'Medida' if attr == 'dax_measures' else 'Coluna') if attr in ('dax_measures', 'dax_columns') else None
                            expressions.append((f"{attr}: {row.get('TableName', '')} {row.get('Name', '')}", value, owner))
                if attr in ('tmschema_calculation_items', 'tmschema_calculation_expressions', 'tmschema_functions', 'tmschema_kpis') and rows:
                    complete = False
                    warnings.append('Grupos de cálculo, KPIs ou funções DAX exigem revisão adicional nesta versão.')
            except Exception:
                complete = False
                warnings.append('Não foi possível ler todas as dependências em ' + attr + '.')
        try:
            table_rows = records(model.tmschema_tables)
            column_rows = records(model.tmschema_columns)
            table_names = {str(r['ID']): r['Name'] for r in table_rows}
            column_names = {}
            for row in column_rows:
                name = row.get('ExplicitName') or row.get('Name') or row.get('InferredName')
                table = table_names.get(str(row.get('TableID')))
                if name and table:
                    column_names[str(row['ID'])] = (table, name)
            if len(column_names) < len(columns):
                raise ValueError('Inventário estrutural incompleto')
            structural_rows = [('Ordenação e propriedades da coluna', column_rows)]
            for attr, label in [('tmschema_levels', 'Nível de hierarquia'),
                                ('tmschema_group_by_columns', 'Agrupamento estrutural'),
                                ('tmschema_variations', 'Variação e hierarquia de data'),
                                ('tmschema_column_permissions', 'Segurança de coluna'),
                                ('tmschema_related_column_details', 'Dependência entre colunas'),
                                ('tmschema_alternate_of', 'Agregação / coluna alternativa'),
                                ('tmschema_calendar_column_refs', 'Calendário'),
                                ('tmschema_calendar_column_groups', 'Grupo de calendário')]:
                structural_rows.append((label, records(getattr(model, attr))))
            for label, rows in structural_rows:
                for row in rows:
                    for key, value in row.items():
                        normal = key.replace('_', '').casefold()
                        if value is not None and str(value) not in ('0', '-1') and normal.endswith('columnid'):
                            # A column's own ID is not a usage. Foreign column IDs are structural references.
                            if label == 'Ordenação e propriedades da coluna' and normal == 'columnid':
                                continue
                            pair = column_names.get(str(value))
                            if pair:
                                structural['roots'].append((*pair, label))
                            else:
                                raise ValueError('Referência estrutural não resolvida')
            structural['complete'] = True
        except Exception:
            warnings.append('Parte das dependências estruturais de colunas não foi confirmada; colunas sem uso ficam em Analisar.')
        # Validate inventory instead of silently assuming schema field names.
        if any(not (r.get('TableName') and (r.get('Name') or r.get('ColumnName'))) for r in columns + measures):
            complete = False
            warnings.append('Parte do inventário tem estrutura não reconhecida.')
    except Exception:
        complete = False
        warnings.append('Modelo não extraído integralmente. PBIX protegido, conexão remota ou formato incompatível podem exigir outra abordagem.')
    finally:
        if model is not None:
            model.close()
    items, complete, warnings = classify(measures, columns, expressions, relationships, layout, complete, warnings, structural)
    if not tables and not items:
        raise ValueError('Não foi possível extrair o modelo deste PBIX. Relatórios com modelo remoto não contêm todo o inventário necessário.')
    summary = {'tables': len(tables), 'measures': sum(i['type'] == 'Medida' for i in items),
               'columns': sum(i['type'] == 'Coluna' for i in items), 'pages': len(layout['sections']) if layout else None,
               'used': sum(i['status'] == 'used' for i in items),
               'unused': sum(i['status'] == 'unused' for i in items),
               'unused_dependency': sum(i['status'] == 'unused_dependency' for i in items),
               'review': sum(i['status'] == 'review' for i in items)}
    return {'filename': filename, 'summary': summary, 'items': items, 'warnings': warnings,
            'coverage': 'limited' if not complete else 'expanded',
            'limitations': 'Análise estática conservadora do arquivo enviado, sem executar DAX. Referências de outros relatórios, Excel, modelos compartilhados e usos externos não são verificadas. Colunas sem uso só são classificadas quando a leitura estrutural é confirmada; casos não cobertos permanecem em Analisar. Não modifica nem exclui itens do PBIX.'}
