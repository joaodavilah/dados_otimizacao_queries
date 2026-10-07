import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from app.services.pbix_extractor import classify, dax_refs, read_layout
from app.main import app
from fastapi.testclient import TestClient

MEASURES = [{'TableName':'Vendas','Name': name} for name in ('Total','Base','Sem uso')]
COLUMNS = [{'TableName':'Vendas','ColumnName':'Valor'}, {'TableName':'Vendas','ColumnName':'Id'}]
LAYOUT = {'sections': [{'visualContainers':[{'config': json.dumps({'singleVisual': {'visualType':'card', 'query': {'Measure': {'Property':'Total'}}}})}]}]}

class AnalysisTests(unittest.TestCase):
    def test_dependencies_and_unused_measure(self):
        items, _, _ = classify(MEASURES,COLUMNS,[('Total','[Base]'),('Base','SUM(Vendas[Valor])')],[],LAYOUT,True,[])
        result = {i['name']:i['status'] for i in items}
        self.assertEqual(result, {'Total':'used','Base':'used','Sem uso':'candidate','Valor':'used','Id':'review'})
    def test_incomplete_never_candidate(self):
        items,_,_ = classify(MEASURES,COLUMNS,[],[],None,False,['layout ausente'])
        self.assertTrue(all(i['status']=='review' for i in items))
    def test_custom_visual_blocks_candidates(self):
        items, complete, _ = classify(MEASURES,[],[],[],{'sections':[{'visualType':'custom123'}]},True,[])
        self.assertFalse(complete)
        self.assertTrue(all(i['status']=='review' for i in items))
    def test_unknown_reference_blocks_candidates(self):
        items,complete,_ = classify(MEASURES,[],[],[],{'sections':[{'Measure':{'Property':'Remota'}}]},True,[])
        self.assertFalse(complete)
        self.assertTrue(all(i['status']=='review' for i in items))
    def test_dax_strings_and_comments(self):
        self.assertEqual(dax_refs('[Base] + "[Ignorar]" // [Outro]\n/*[Falso]*/ [A]]B]'), ['Base','A]B'])
    def test_relationship_protection(self):
        items,_,_ = classify([],COLUMNS,[],[{'FromTableName':'Vendas','FromColumnName':'Id'}],LAYOUT,True,[])
        self.assertEqual(next(i for i in items if i['name']=='Id')['status'],'used')
    def test_ambiguous_names_protect_all(self):
        measures = MEASURES + [{'TableName':'Outra','Name':'Base'}]
        items,_,_ = classify(measures,[],[('Total','[Base]')],[],LAYOUT,True,[])
        self.assertTrue(all(i['status']=='used' for i in items if i['name']=='Base'))
    def test_utf16_layout(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'sample.pbix'
            with zipfile.ZipFile(path,'w') as z:z.writestr('Report/Layout',json.dumps(LAYOUT).encode('utf-16-le'))
            self.assertEqual(read_layout(path),LAYOUT)
    def test_missing_layout(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'sample.pbix'
            with zipfile.ZipFile(path,'w') as z:z.writestr('dummy','x')
            with self.assertRaises(ValueError):read_layout(path)
    def test_auth_required(self):
        with patch.dict('os.environ',{'SUPABASE_URL':'https://test.example','SUPABASE_PUBLISHABLE_KEY':'public'}):
            with TestClient(app) as client:
                self.assertEqual(client.post('/api/pbix/extract',files={'file':('x.pbix',b'x')}).status_code,401)
    def test_invalid_extension(self):
        async def fake_auth(_):return 'user-test'
        with patch('app.main.authenticate',fake_auth):
            with TestClient(app) as client:
                self.assertEqual(client.post('/api/pbix/extract',files={'file':('x.txt',b'x')}).status_code,400)
    def test_empty_file(self):
        async def fake_auth(_):return 'user-test'
        with patch('app.main.authenticate',fake_auth):
            with TestClient(app) as client:
                self.assertEqual(client.post('/api/pbix/extract',files={'file':('x.pbix',b'')}).status_code,400)
    def test_upload_cleanup(self):
        async def fake_auth(_):return 'user-test'
        paths=[]
        def fake_extract(path,filename):
            paths.append(path)
            self.assertTrue(path.exists())
            return {'filename':filename}
        with patch('app.main.authenticate',fake_auth),patch('app.main.extract_pbix',fake_extract):
            with TestClient(app) as client:
                response=client.post('/api/pbix/extract',files={'file':('x.pbix',b'content')})
                self.assertEqual(response.status_code,200)
        self.assertFalse(paths[0].exists())

if __name__=='__main__':unittest.main()
