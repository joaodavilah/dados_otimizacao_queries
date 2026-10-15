import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import apsw
from app.services.model_metadata import EmbeddedMetadata, model_inventory, model_dependencies
from app.services.pbix_extractor import classify, extract_pbix
from app.services.dax_dependencies import references


class MetadataTests(unittest.TestCase):
    def setUp(self):
        self.conn = apsw.Connection(':memory:')
        self.conn.execute('CREATE TABLE "Table" (ID INTEGER, Name TEXT)')
        self.conn.execute('CREATE TABLE "Column" (ID INTEGER, TableID INTEGER, Type INTEGER, ExplicitName TEXT, InferredName TEXT, SortByColumnID INTEGER, Expression TEXT)')
        self.conn.execute('CREATE TABLE Measure (ID INTEGER, TableID INTEGER, Name TEXT, Expression TEXT, FormatStringDefinitionID INTEGER, DetailRowsDefinitionID INTEGER)')
        self.conn.execute('INSERT INTO "Table" VALUES (1,\'Vendas\')')
        self.conn.execute('INSERT INTO "Column" VALUES (10,1,1,\'Mês\',NULL,11,NULL),(11,1,1,\'Ordem\',NULL,NULL,NULL),(12,1,4,NULL,\'Calculada\',NULL,NULL),(13,1,3,\'RowNumber\',NULL,NULL,NULL)')
        self.conn.execute('INSERT INTO Measure VALUES (20,1,\'Total\',\'SUM(Vendas[Calculada])\',30,NULL),(21,1,\'Sem uso\',\'1\',NULL,NULL)')
        self.model = SimpleNamespace(_metadata=SimpleNamespace(source=SimpleNamespace(_db=SimpleNamespace(conn=self.conn))))
        self.meta = EmbeddedMetadata(self.model)

    def tearDown(self):
        self.conn.close()

    def analyze(self, layout):
        self.meta = EmbeddedMetadata(self.model)
        _, measures, columns, relations, names, column_ids, measure_ids = model_inventory(self.meta)
        expressions, structural, complete, warnings = model_dependencies(self.meta, names, column_ids, measure_ids)
        return classify(measures, columns, expressions, relations, layout, complete, warnings, structural)

    def add_format(self):
        self.conn.execute('CREATE TABLE FormatStringDefinition (ID INTEGER, Expression TEXT)')
        self.conn.execute('INSERT INTO FormatStringDefinition VALUES (30,\'IF(Vendas[Mês]="a", "0", "0.0")\')')

    def test_logical_inventory_includes_calculated_table_columns_and_no_storage(self):
        _, _, columns, _, _, ids, _ = model_inventory(self.meta)
        self.assertEqual([c['ColumnName'] for c in columns], ['Mês', 'Ordem', 'Calculada'])
        self.assertNotIn('13', ids)

    def test_sorting_is_a_dependency_not_a_blanket_root(self):
        self.add_format()
        items, complete, _ = self.analyze({'sections': [{'Column': {'Property':'Mês','Expression':{'SourceRef':{'Entity':'Vendas'}}}}]})
        self.assertTrue(complete)
        result={i['name']:i for i in items}
        self.assertEqual(result['Mês']['status'],'used')
        self.assertEqual(result['Ordem']['status'],'used')
        self.assertEqual(result['Calculada']['status'],'unused_dependency')
        self.assertIn('Vendas[Mês]',result['Ordem']['usage_path'])

    def test_unused_sorting_chain_stays_unused(self):
        self.add_format()
        items, _, _ = self.analyze({'sections': []})
        result={i['name']:i['status'] for i in items}
        self.assertEqual(result['Mês'],'unused_dependency')  # dynamic format of unused Total
        self.assertEqual(result['Ordem'],'unused_dependency')
        self.assertEqual(result['Total'],'unused')

    def test_dynamic_format_follows_its_measure(self):
        self.add_format()
        items, complete, _ = self.analyze({'sections':[{'Measure':{'Property':'Total'}}]})
        result={i['name']:i for i in items}
        self.assertTrue(complete)
        self.assertEqual(result['Mês']['status'],'used')
        self.assertEqual(result['Ordem']['status'],'used')
        self.assertEqual(result['Sem uso']['status'],'unused')
        self.assertIn('Vendas[Total]',result['Mês']['usage_path'])

    def test_missing_format_definition_blocks_negatives(self):
        items, complete, _ = self.analyze({'sections':[]})
        self.assertFalse(complete)
        self.assertTrue(all(i['status']=='review' for i in items))

    def test_missing_sort_property_only_blocks_columns(self):
        self.add_format()
        self.conn.execute('ALTER TABLE "Column" DROP COLUMN SortByColumnID')
        items, complete, _ = self.analyze({'sections':[]})
        self.assertFalse(complete)
        self.assertEqual(next(i for i in items if i['name']=='Sem uso')['status'],'unused')
        self.assertTrue(all(i['status']=='review' for i in items if i['type']=='Coluna'))

    def test_groupby_resolves_related_column_owner(self):
        self.add_format()
        self.conn.execute('CREATE TABLE RelatedColumnDetails (ID INTEGER, ColumnID INTEGER)')
        self.conn.execute('CREATE TABLE GroupByColumn (ID INTEGER, RelatedColumnDetailsID INTEGER, GroupingColumnID INTEGER)')
        self.conn.execute('INSERT INTO RelatedColumnDetails VALUES (40,12)')
        self.conn.execute('INSERT INTO GroupByColumn VALUES (41,40,10)')
        items, complete, _ = self.analyze({'sections':[{'Column':{'Property':'Calculada'}}]})
        self.assertTrue(complete)
        self.assertTrue(all(i['status']=='used' for i in items if i['type']=='Coluna'))

    def test_relationship_column_ids_resolve(self):
        self.add_format()
        self.conn.execute('CREATE TABLE Relationship (ID INTEGER, FromColumnID INTEGER, ToColumnID INTEGER)')
        self.conn.execute('INSERT INTO Relationship VALUES (50,10,12)')
        items, complete, _ = self.analyze({'sections':[]})
        self.assertTrue(complete)
        self.assertTrue(all(i['status']=='used' for i in items if i['type']=='Coluna'))

    def test_unknown_relationship_never_allows_negatives(self):
        self.conn.execute('CREATE TABLE Relationship (ID INTEGER, FromColumnID INTEGER, ToColumnID INTEGER)')
        self.conn.execute('INSERT INTO Relationship VALUES (50,999,12)')
        with self.assertRaises(ValueError):
            model_inventory(EmbeddedMetadata(self.model))

    def test_kpi_dependencies_are_resolved_without_global_review(self):
        self.add_format()
        self.conn.execute('CREATE TABLE KPI (ID INTEGER, MeasureID INTEGER, TargetExpression TEXT)')
        self.conn.execute('INSERT INTO KPI VALUES (50,20,\'SUM(Vendas[Calculada])\')')
        items, complete, _ = self.analyze({'sections':[]})
        self.assertTrue(complete)
        self.assertEqual(next(i for i in items if i['name']=='Total')['status'],'used')
        self.assertEqual(next(i for i in items if i['name']=='Sem uso')['status'],'unused')

    def test_report_hierarchy_level_resolves(self):
        self.add_format()
        self.conn.execute('CREATE TABLE Hierarchy (ID INTEGER, TableID INTEGER, Name TEXT)')
        self.conn.execute('CREATE TABLE Level (ID INTEGER, HierarchyID INTEGER, ColumnID INTEGER, Name TEXT)')
        self.conn.execute('INSERT INTO Hierarchy VALUES (60,1,\'Calendário\')')
        self.conn.execute('INSERT INTO Level VALUES (61,60,10,\'Mês\')')
        layout={'sections':[{'HierarchyLevel':{'Level':'Mês','Expression':{'Hierarchy':{'Hierarchy':'Calendário','Expression':{'SourceRef':{'Entity':'Vendas'}}}}}}]}
        items, complete, _ = self.analyze(layout)
        self.assertTrue(complete)
        self.assertEqual(next(i for i in items if i['name']=='Mês')['status'],'used')

    def test_reader_is_read_only(self):
        before=self.conn.total_changes()
        model_inventory(self.meta)
        self.assertEqual(self.conn.total_changes(), before)

    def test_legacy_column_and_relationship_field_names(self):
        self.add_format()
        self.conn.execute('ALTER TABLE "Column" RENAME COLUMN Type TO BindingType')
        self.conn.execute('CREATE TABLE Relationship (ID INTEGER, FromEndColumnID INTEGER, ToEndColumnID INTEGER)')
        self.conn.execute('INSERT INTO Relationship VALUES (50,10,12)')
        items, complete, _ = self.analyze({'sections':[]})
        self.assertTrue(complete)
        self.assertTrue(all(i['status']=='used' for i in items if i['type']=='Coluna'))

    def test_declared_key_is_preserved_even_without_a_visual(self):
        self.add_format()
        self.conn.execute('ALTER TABLE "Column" ADD COLUMN IsKey INTEGER')
        self.conn.execute('UPDATE "Column" SET IsKey=1 WHERE ID=12')
        items, complete, _ = self.analyze({'sections':[]})
        self.assertTrue(complete)
        self.assertEqual(next(i for i in items if i['name']=='Calculada')['status'],'used')
        self.assertEqual(next(i for i in items if i['name']=='Sem uso')['status'],'unused')

    def test_internal_table_objects_are_not_removal_candidates(self):
        self.add_format()
        self.conn.execute('ALTER TABLE "Table" ADD COLUMN SystemFlags INTEGER')
        self.conn.execute('UPDATE "Table" SET SystemFlags=1')
        items, complete, _ = self.analyze({'sections':[]})
        self.assertTrue(complete)
        self.assertTrue(all(i['status']=='used' for i in items))

    def test_extract_integration_with_embedded_metadata_adapter(self):
        self.add_format()
        self.model.close=lambda: None
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'test.pbix'
            with zipfile.ZipFile(path,'w') as z:
                z.writestr('Report/Layout',json.dumps({'sections':[{'Measure':{'Property':'Total'}}]}))
            with patch('pbixray.PBIXRay',return_value=self.model):
                report=extract_pbix(path,'test.pbix')
        self.assertEqual(report['analysis_version'],14)
        self.assertEqual(report['summary']['columns'],3)
        self.assertEqual(report['coverage'],'expanded')
        self.assertEqual(report['summary']['review'],0)


class ReferenceTests(unittest.TestCase):
    def test_return_var_and_escaped_qualified_identifiers(self):
        refs, tables, _, problems=references("VAR x = SUM('Venda''s'[A]]B]) RETURN x + [Base]",["Venda's"])
        self.assertEqual(refs,[("Venda's",'A]B'),(None,'Base')])
        self.assertEqual(tables,set())
        self.assertEqual(problems,[])

    def test_virtual_column_does_not_make_entire_inventory_uncertain(self):
        measures=[{'TableName':'Vendas','Name':'Total'},{'TableName':'Vendas','Name':'Sem uso'}]
        cols=[{'TableName':'Vendas','ColumnName':'Valor'}]
        sql='VAR t = SELECTCOLUMNS(Vendas, "Temporária", Vendas[Valor]) RETURN SUMX(t, [Temporária])'
        items, complete, _=classify(measures,cols,[('Total',sql)],[],{'sections':[{'Measure':{'Property':'Total'}}]},True,[],{'complete':True})
        self.assertTrue(complete)
        self.assertEqual({i['name']:i['status'] for i in items},{'Total':'used','Sem uso':'unused','Valor':'used'})

    def test_unknown_owned_reference_is_scoped_and_explained(self):
        measures=[{'TableName':'T','Name':n} for n in ['A','B','Independente']]
        items, complete, _=classify(measures,[],[('A','[Não extraída]+[B]')],[],{'sections':[]},True,[])
        self.assertFalse(complete)
        self.assertEqual({i['name']:i['status'] for i in items},{'A':'review','B':'review','Independente':'unused'})
        self.assertIn('Não extraída',items[0]['review_reasons'][0])

    def test_visual_aliases_are_scoped_per_query(self):
        measures=[{'TableName':'T','Name':'A'},{'TableName':'Outra','Name':'A'}]
        layout={'sections':[{'visuals':[{'query':{'From':[{'Name':'x','Entity':'T'}],'Select':[{'Measure':{'Property':'A','Expression':{'SourceRef':{'Source':'x'}}}}]}},
                                         {'query':{'From':[{'Name':'x','Entity':'Outra'}],'Select':[]}}]}]}
        items, complete, _=classify(measures,[],[],[],layout,True,[])
        self.assertTrue(complete)
        self.assertEqual([i['status'] for i in items],['used','unused'])

    def test_title_and_queryref_prefix_do_not_prove_use(self):
        measures=[{'TableName':'T','Name':n} for n in ['A','Antiga']]
        layout={'sections':[{'title':'[A] e T.A','queryRef':'T.Antiga'}]}
        items, complete, _=classify(measures,[],[],[],layout,True,[])
        self.assertTrue(complete)
        self.assertEqual([i['status'] for i in items],['unused','used'])

    def test_queryref_without_kind_does_not_prove_both_measure_and_column_use(self):
        measures=[{'TableName':'T','Name':'A'}]
        columns=[{'TableName':'T','ColumnName':'A'}]
        items, complete, _=classify(measures,columns,[],[],{'sections':[{'queryRef':'T.A'}]},True,[],{'complete':True})
        self.assertFalse(complete)
        self.assertEqual([i['status'] for i in items],['review','review'])

    def test_malformed_expression_never_proves_non_use(self):
        measures=[{'TableName':'T','Name':n} for n in ['A','B']]
        items, complete, _=classify(measures,[],[('A','"incompleta [B]')],[],{'sections':[]},True,[])
        self.assertFalse(complete)
        self.assertTrue(all(i['status']=='review' for i in items))

    def test_usage_path_explains_chain_and_cycles_terminate(self):
        measures=[{'TableName':'T','Name':n} for n in ['A','B','C']]
        items, _, _=classify(measures,[],[('A','[B]'),('B','[C]'),('C','[B]')],[],{'sections':[{'Measure':{'Property':'A'}}]},True,[])
        self.assertEqual(items[2]['usage_path'][1:],['T[A]','T[B]','T[C]'])
        self.assertEqual(items[2]['used_by'],['T[B]'])

    def test_field_parameter_nameof_dependency(self):
        measures=[{'TableName':'T','Name':n} for n in ['Escolhida','Sem uso']]
        items, complete, _=classify(measures,[],[('Tabela de parâmetro','{("Valor", NAMEOF(T[Escolhida]), 0)}',None)],[],{'sections':[]},True,[])
        self.assertTrue(complete)
        self.assertEqual([i['status'] for i in items],['used','unused'])

if __name__ == '__main__':
    unittest.main()
