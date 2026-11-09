"""Offline paired-export discovery; no production snapshots or scientific writes."""
import json
import unittest
import uuid
import struct
from unittest.mock import patch

import reader


class ExportDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.root=reader.HERE/'state'/('export-discovery-'+uuid.uuid4().hex)
        self.out=self.root/'output'
        self.figure=self.out/'01_results_and_figures/n=25'
        self.data=self.out/'02_run_level_data/n=25'
        self.figure.mkdir(parents=True);self.data.mkdir(parents=True)
        self.addCleanup(self.clean)
        for name,value in (('OUT',self.out),('CODE',self.root/'code')):
            p=patch.object(reader,name,value);p.start();self.addCleanup(p.stop)
        # PNG metadata fixture; no scientific rendering or production data.
        self.names=['nyquist.png','bode_magnitude.png','bode_phase.png',
                    'residual_real.png','residual_imag.png','residual_real_normalized.png',
                    'residual_imag_normalized.png','distribution_pilot.png',
                    '05_mitigation_median_CI_convergence.png','06_mitigation_three_tier_positioning.png']
        self.manifest={'n':25,'partial_diagnostic_only':False,'figures':{},'csv_sha256':{}}
        for name in self.names:
            def chunk(kind,data):return struct.pack('>I4s',len(data),kind)+data+b'\0'*4
            p=self.figure/name;p.write_bytes(b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>II',4800,4800))+chunk(b'pHYs',struct.pack('>IIB',23622,23622,1))+chunk(b'IDAT',b''))
            self.manifest['figures'][name]={'valid':True,'sha256':reader.sha(p)}
        self.write_pair()

    def clean(self):
        for p in sorted(self.root.rglob('*'),key=lambda p:len(p.parts),reverse=True):
            if p.is_file():p.unlink()
            else:p.rmdir()
        self.root.rmdir()

    def write_pair(self):
        for folder in (self.figure,self.data):
            (folder/'export_manifest.json').write_text(json.dumps(self.manifest))

    def test_discovers_all_ten_in_established_n25_directory(self):
        r=reader.Reader();items=r.catalog()
        self.assertEqual({x['name'] for x in items},set(self.names))
        self.assertTrue(all(x['level']==25 and x['version']=='direct' for x in items))
        self.assertTrue(all(r.artifact(x['id']).parent==self.figure for x in items))
        self.assertFalse((self.root/'01_results_and_figures').exists())

    def test_missing_pair_and_modified_png_are_not_advertised(self):
        (self.figure/'export_manifest.json').unlink()
        self.assertEqual(reader.Reader().catalog(),[])
        self.write_pair();(self.figure/self.names[0]).write_bytes(b'changed')
        self.assertEqual(len(reader.Reader().catalog()),9)

    def test_versioned_pair_discovery_preserves_scope(self):
        for attr in ('figure','data'):
            base=getattr(self,attr);version=base/'v001-fixture';version.mkdir()
            for p in list(base.iterdir()):
                if p.is_file():p.rename(version/p.name)
            setattr(self,attr,version)
        items=reader.Reader().catalog()
        self.assertEqual(len(items),10)
        self.assertTrue(all(x['level']==25 and x['version']=='v001-fixture' for x in items))

    def test_publication_manifest_does_not_hide_export_figures(self):
        for base in (self.figure,self.data):(base/'publication_manifest.json').write_text(json.dumps({'n':25,'figures':{}}))
        self.assertEqual(len(reader.Reader().catalog()),10)

    def test_contract_includes_positioning_and_optional_is_not_required(self):
        from figure_registry import contract,inventory
        c=contract();self.assertIn('06_mitigation_three_tier_positioning.png',c['required'])
        self.assertEqual(len(c['required']),10);self.assertEqual(len(c['optional']),1)
        snapshot=inventory(self.out,25)['versions'][0]
        self.assertEqual(snapshot['verified_pngs'],10);self.assertEqual(snapshot['missing_required'],[])

    def test_wrong_dpi_and_unreferenced_files_are_not_certified(self):
        from figure_registry import inventory
        path=self.figure/self.names[0];path.write_bytes(path.read_bytes().replace(struct.pack('>I',23622),struct.pack('>I',11811)))
        self.manifest['figures'][path.name]['sha256']=reader.sha(path);self.write_pair()
        self.assertEqual(len(reader.Reader().catalog()),9)
        del self.manifest['figures'][self.names[1]];self.write_pair()
        report=inventory(self.out,25)['versions'][0]
        self.assertEqual(next(f for f in report['figures'] if f['name']==self.names[1])['reference_status'],'Missing manifest reference')
        self.assertEqual(len(reader.Reader().catalog()),8)

    def test_inventory_is_read_only_and_does_not_copy_pngs(self):
        before={str(p):reader.sha(p) for p in self.root.rglob('*') if p.is_file()}
        reader.Reader().catalog()
        self.assertEqual(before,{str(p):reader.sha(p) for p in self.root.rglob('*') if p.is_file()})


if __name__=='__main__':unittest.main()
