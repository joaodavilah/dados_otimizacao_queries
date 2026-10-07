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
    with zipfile.ZipFile(path) as archive:
        members = [x for x in archive.infolist() if x.filename.lower() == 'report/layout']
        if len(members) != 1:
            raise ValueError('Layout clássico do relatório ausente ou não suportado.')
        if members[0].file_size > MAX_LAYOUT:
            raise ValueError('Layout do relatório excede o limite de 24 MB.')
        blob = archive.read(members[0])
    for encoding in ('utf-16', 'utf-16-le', 'utf-8-sig'):
        try:
            value = json.loads(blob.decode(encoding))
            if not isinstance(value, dict) or not isinstance(value.get('sections'), list):
                raise ValueError('Estrutura de páginas não reconhecida.')
            return value
        except (UnicodeError, json.JSONDecodeError):
            continue
    raise ValueError('Não foi possível interpretar o layout do relatório.')


def dax_refs(expression):
    # Ignore strings/comments, preserving quoted table names and bracket identifiers.
    token = re.compile(r'"(?:""|[^"])*"|//[^\n]*|--[^\n]*|/\*.*?\*/|\[(?:\]\]|[^\]])+\]', re.S)
    refs = []
    for match in token.finditer(expression or ''):
        value = match.group()
        if value.startswith('['):
            refs.append(value[1:-1].replace(']]', ']'))
    return refs


def classify(measures, columns, expressions, relationships, layout, complete, warnings):
    items = []
    for kind, source in [('Medida', measures), ('Coluna', columns)]:
        for row in source:
            name = row.get('Name') or row.get('ColumnName')
            table = row.get('TableName')
            if name and table:
                items.append({'type': kind, 'table': str(table), 'name': str(name), 'evidence': []})
    by_name = defaultdict(list)
    for item in items:
        by_name[item['name'].casefold()].append(item)

    def mark(name, reason, table=None, kind=None):
        found = False
        for item in by_name.get(str(name).casefold(), []):
            if table and item['table'].casefold() != str(table).casefold():
                continue
            if kind and item['type'] != kind:
                continue
            if reason not in item['evidence']:
                item['evidence'].append(reason)
            found = True
        return found

    for label, expression in expressions:
        for name in dax_refs(expression):
            # Unqualified or ambiguous names protect every matching item.
            mark(name, 'Dependência em ' + label)
    for relation in relationships:
        for side in ('From', 'To'):
            mark(relation.get(side + 'ColumnName', ''), 'Participa de relacionamento', relation.get(side + 'TableName'), 'Coluna')

    if layout is not None:
        for path, node in walk(layout):
            if isinstance(node, dict):
                for field, kind in [('Measure', 'Medida'), ('Column', 'Coluna')]:
                    ref = node.get(field)
                    if isinstance(ref, dict) and ref.get('Property'):
                        if not mark(ref['Property'], 'Referência no relatório: ' + path, kind=kind):
                            complete = False
                            warnings.append('O relatório contém referências sem correspondência no modelo extraído.')
                visual = node.get('visualType')
                if visual and str(visual).casefold() not in {
                    'tableex','pivotTable'.casefold(),'card','cardvisual','multirowcard','slicer',
                    'barchart','columnchart','clusteredbarchart','clusteredcolumnchart','stackedbarchart',
                    'stackedcolumnchart','hundredpercentstackedbarchart','hundredpercentstackedcolumnchart',
                    'linechart','areachart','stackedareachart','piechart','donutchart','scatterchart',
                    'waterfallchart','treemap','gauge','kpi','map','filledmap','textbox','image','shape',
                    'actionbutton','lineclusteredcolumncombochart','linestackedcolumncombochart'}:
                    complete = False
                    warnings.append('Visual não coberto nesta versão: ' + str(visual) + '. Itens sem uso ficam para revisão.')
            elif isinstance(node, str):
                for name in dax_refs(node):
                    mark(name, 'Referência textual no relatório: ' + path)
                # queryRef convention Table.Measure; false positives retain items.
                for item in items:
                    if (item['table'] + '.' + item['name']).casefold() in node.casefold():
                        mark(item['name'], 'Referência textual no relatório: ' + path, item['table'], item['type'])

    for item in items:
        if item['evidence']:
            item['status'] = 'used'
            item['reason'] = item['evidence'][0]
        elif not complete or item['type'] == 'Coluna':
            item['status'] = 'review'
            item['reason'] = ('Sem referência encontrada, mas a leitura tem limitações.' if not complete else
                              'Sem referência explícita encontrada. Uso de tabela inteira, ordenação, hierarquias e propriedades estruturais exigem revisão.')
        else:
            item['status'] = 'candidate'
            item['reason'] = 'Sem referência encontrada no layout clássico ou nas expressões lidas. Candidata à revisão para remoção; não é garantia de ausência de uso.'
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
                            expressions.append((f"{attr}: {row.get('TableName', '')} {row.get('Name', '')}", value))
                if attr in ('tmschema_calculation_items', 'tmschema_calculation_expressions', 'tmschema_functions', 'tmschema_kpis') and rows:
                    complete = False
                    warnings.append('Grupos de cálculo, KPIs ou funções DAX exigem revisão adicional nesta versão.')
            except Exception:
                complete = False
                warnings.append('Não foi possível ler todas as dependências em ' + attr + '.')
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
    items, complete, warnings = classify(measures, columns, expressions, relationships, layout, complete, warnings)
    if not tables and not items:
        raise ValueError('Não foi possível extrair o modelo deste PBIX. Relatórios com modelo remoto não contêm todo o inventário necessário.')
    summary = {'tables': len(tables), 'measures': sum(i['type'] == 'Medida' for i in items),
               'columns': sum(i['type'] == 'Coluna' for i in items), 'pages': len(layout['sections']) if layout else None,
               'used': sum(i['status'] == 'used' for i in items),
               'candidates': sum(i['status'] == 'candidate' for i in items),
               'review': sum(i['status'] == 'review' for i in items)}
    return {'filename': filename, 'summary': summary, 'items': items, 'warnings': warnings,
            'coverage': 'limited' if not complete else 'classic',
            'limitations': 'Análise estática conservadora do arquivo enviado, sem executar DAX. Referências de outros relatórios, Excel, modelos compartilhados e usos externos não são verificadas. Colunas sem uso explícito exigem revisão. Não modifica nem exclui itens do PBIX.'}
