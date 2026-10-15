"""Strict read-only metadata adapter for the pinned pbixray 0.15.5.

Its public schema is a storage inventory and omits some logical columns; its
TMSCHEMA views also omit SortByColumnID. Use the embedded SQLite connection
for these fields. Fail explicitly if the internal adapter changes.
"""
from collections import defaultdict

TABLES = {
    'Table', 'Column', 'Measure', 'Relationship', 'Hierarchy', 'Level',
    'GroupByColumn', 'RelatedColumnDetails', 'Variation', 'AlternateOf',
    'ColumnPermission', 'CalendarColumnReference', 'CalendarColumnGroup',
    'KPI', 'FormatStringDefinition', 'DetailRowsDefinition', 'Partition',
    'CalculationGroup', 'CalculationItem', 'CalculationExpression',
    'Function', 'TablePermission',
}


class EmbeddedMetadata:
    def __init__(self, model):
        self.conn = model._metadata.source._db.conn
        self.available = {r[0].casefold() for r in self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        self.cache = {}
        self.fields = {}

    def rows(self, table, required=False):
        if table not in TABLES:
            raise ValueError('Tabela de metadados não permitida.')
        if table.casefold() not in self.available:
            if required:
                raise ValueError('Metadados obrigatórios ausentes: ' + table)
            return []
        if table not in self.cache:
            names = [r[1] for r in self.conn.execute(f'PRAGMA table_info("{table}")')]
            self.fields[table] = set(names)
            self.cache[table] = [dict(zip(names, r)) for r in
                                 self.conn.execute(f'SELECT * FROM "{table}"')]
        return self.cache[table]


def id_key(value):
    return str(value) if value is not None else None


def model_inventory(meta):
    table_rows = meta.rows('Table', required=True)
    column_rows = meta.rows('Column', required=True)
    measure_rows = meta.rows('Measure', required=True)
    names = {id_key(r['ID']): r['Name'] for r in table_rows if r.get('Name')}
    columns, measures, column_ids, measure_ids = [], [], {}, {}
    for row in column_rows:
        # RowNumber is server generated. Include calculated-table columns (4),
        # and logical DirectQuery columns even if there are no VertiPaq files.
        kind = row.get('Type', row.get('BindingType'))
        if kind == 3:
            continue
        name = row.get('ExplicitName') or row.get('Name') or row.get('InferredName')
        table = names.get(id_key(row.get('TableID')))
        if not name or not table or kind not in (1, 2, 4):
            raise ValueError('Inventário lógico de colunas não reconhecido.')
        pair = (str(table), str(name))
        column_ids[id_key(row['ID'])] = pair
        columns.append({'TableName': pair[0], 'ColumnName': pair[1]})
    for row in measure_rows:
        table = names.get(id_key(row.get('TableID')))
        if not row.get('Name') or not table:
            raise ValueError('Inventário lógico de medidas não reconhecido.')
        owner = (str(table), str(row['Name']), 'Medida')
        measure_ids[id_key(row['ID'])] = owner
        measures.append({'TableName': owner[0], 'Name': owner[1]})
    relationships = []
    for row in meta.rows('Relationship'):
        ref = {}
        for side in ('From', 'To'):
            pair = column_ids.get(id_key(row.get(side + 'ColumnID', row.get(side + 'EndColumnID'))))
            if not pair:
                raise ValueError('Coluna de relacionamento não resolvida.')
            ref[side + 'TableName'], ref[side + 'ColumnName'] = pair
        relationships.append(ref)
    return list(names.values()), measures, columns, relationships, names, column_ids, measure_ids


def model_dependencies(meta, names, column_ids, measure_ids):
    """Resolve structural IDs, expression owners and report hierarchy levels."""
    structural = {'roots': [], 'edges': [], 'hierarchies': [], 'complete': True}
    expressions, warnings = [], []
    complete = True
    internal_tables = {id_key(r['ID']) for r in meta.rows('Table') if r.get('SystemFlags')}

    def column(value):
        pair = column_ids.get(id_key(value))
        if pair is None:
            structural['complete'] = False
            warnings.append('Uma dependência estrutural de coluna não pôde ser resolvida pelo ID.')
        return pair

    def dependency(owner, target, label):
        if owner and target:
            structural['edges'].append(((*owner, 'Coluna'), (*target, 'Coluna'), label))

    definitions = defaultdict(list)
    for row in meta.rows('Measure'):
        owner = measure_ids.get(id_key(row['ID']))
        if id_key(row.get('TableID')) in internal_tables or row.get('SystemFlags'):
            structural.setdefault('measure_roots', []).append((*owner[:2], 'Objeto interno do modelo'))
        if row.get('Expression'):
            expressions.append((f'Medida {owner[0]}[{owner[1]}]', row['Expression'], owner))
        for field, table in [('FormatStringDefinitionID', 'FormatStringDefinition'),
                             ('DetailRowsDefinitionID', 'DetailRowsDefinition')]:
            if row.get(field):
                definitions[(table, id_key(row[field]))].append(owner)
    for row in meta.rows('Column'):
        pair = column_ids.get(id_key(row['ID']))
        if pair and (id_key(row.get('TableID')) in internal_tables or row.get('SystemFlags') or row.get('IsKey')):
            structural['roots'].append((*pair, 'Chave da tabela ou objeto interno do modelo'))
        if pair and row.get('Expression'):
            expressions.append((f'Coluna {pair[0]}[{pair[1]}]', row['Expression'], (*pair, 'Coluna')))
        if pair and row.get('SortByColumnID') not in (None, 0, -1):
            dependency(pair, column(row['SortByColumnID']), 'Ordenação da coluna')
    # An absent property is unreadable, not proof there is no sorting.
    if 'SortByColumnID' not in meta.fields.get('Column', set()):
        structural['complete'] = False
        warnings.append('Propriedade de ordenação não disponível neste modelo.')

    hierarchy_rows = {id_key(r['ID']): r for r in meta.rows('Hierarchy')}
    for level in meta.rows('Level'):
        hierarchy = hierarchy_rows.get(id_key(level.get('HierarchyID')))
        pair = column(level.get('ColumnID'))
        if hierarchy and pair:
            table = names.get(id_key(hierarchy.get('TableID')))
            structural['hierarchies'].append({'table': table, 'name': hierarchy.get('Name'),
                                             'level': level.get('Name'), 'column': pair})
            # Keep hierarchy membership as a conservative model-level use.
            structural['roots'].append((*pair, 'Participa da hierarquia ' + str(hierarchy.get('Name'))))
        else:
            structural['complete'] = False
    related = {id_key(r['ID']): r.get('ColumnID') for r in meta.rows('RelatedColumnDetails')}
    for row in meta.rows('GroupByColumn'):
        owner_id = row.get('OwnerColumnID') or related.get(id_key(row.get('RelatedColumnDetailsID')))
        dependency(column(owner_id), column(row.get('GroupingColumnID')), 'Agrupamento da coluna / parâmetro de campo')
    for table, label in [('Variation', 'Variação e hierarquia de data'),
                         ('AlternateOf', 'Agregação / coluna alternativa'),
                         ('ColumnPermission', 'Segurança de coluna'),
                         ('CalendarColumnReference', 'Calendário')]:
        for row in meta.rows(table):
            for key, value in row.items():
                if key.casefold().endswith('columnid') and value not in (None, 0, -1):
                    pair = column(value)
                    if pair:
                        structural['roots'].append((*pair, label))
    for row in meta.rows('TablePermission'):
        if row.get('FilterExpression'):
            expressions.append(('Segurança RLS: ' + str(names.get(id_key(row.get('TableID')), '')),
                                row['FilterExpression'], None))
    for row in meta.rows('Partition'):
        if row.get('Type') == 2 and row.get('QueryDefinition'):
            expressions.append(('Tabela calculada ' + str(names.get(id_key(row.get('TableID')), '')),
                                row['QueryDefinition'], None))
    for table in ('FormatStringDefinition', 'DetailRowsDefinition'):
        found_ids = set()
        for row in meta.rows(table):
            found_ids.add(id_key(row['ID']))
            if row.get('Expression'):
                owners = definitions.get((table, id_key(row['ID'])), [])
                label = 'Formatação dinâmica' if table == 'FormatStringDefinition' else 'Linhas de detalhe'
                # Ownerless table/group expressions remain conservative roots.
                for owner in owners or [None]:
                    expressions.append((label + (f' de {owner[0]}[{owner[1]}]' if owner else ''), row['Expression'], owner))
        if any(t == table and key not in found_ids for t, key in definitions):
            complete = False
            warnings.append('Uma definição de formatação ou detalhe não pôde ser lida.')
    for row in meta.rows('KPI'):
        owner = measure_ids.get(id_key(row.get('MeasureID')))
        if owner is None:
            complete = False
            warnings.append('Medida de um KPI não resolvida.')
        for key in ('TargetExpression', 'StatusExpression', 'TrendExpression'):
            if row.get(key):
                expressions.append(('KPI ' + key, row[key], owner))
        # KPI existence is treated as structural use, preserving the measure.
        if owner:
            structural['measure_roots'] = structural.get('measure_roots', []) + [(*owner[:2], 'Medida de KPI')]
    for table in ('CalculationItem', 'CalculationExpression', 'Function'):
        rows = meta.rows(table)
        for row in rows:
            for key, value in row.items():
                if isinstance(value, str) and ('expression' in key.casefold() or key.casefold() == 'definition'):
                    expressions.append((table + ': ' + str(row.get('Name', '')), value, None))
        if rows:
            complete = False
            warnings.append('Grupos de cálculo ou funções DAX ainda podem ter referências dinâmicas; ausência de uso exige revisão.')
    return expressions, structural, complete, warnings
