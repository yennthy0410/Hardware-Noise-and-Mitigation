"""Deterministic regression checks for the existing VQE publication plot helper.

These pin observable numerical/output behavior; they deliberately do not
replace or bless an undocumented historical source hash.
"""
from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd
from PIL import Image

CODE=Path(__file__).resolve().parents[1]
ROOT=CODE.parents[2]
sys.path.insert(0,str(CODE))
from repository_paths import create_scratch_dir,cleanup_scratch_dir
import mitigation_pilot as pilot


class ScientificPlotContractTests(unittest.TestCase):
    def setUp(self):
        self.scratch=create_scratch_dir("sp",ROOT)
        self.addCleanup(cleanup_scratch_dir,self.scratch,ROOT)

    def test_bootstrap_medians_and_intervals_are_deterministic(self):
        rows=[]
        tables=[]
        for treatment_index,(name,*_) in enumerate(pilot.TREATMENTS):
            values=np.arange(1,5,dtype=float)+treatment_index
            rows.extend({"treatment":name,"pub":index,"SSE":value}
                        for index,value in enumerate(values,1))
            tables.append({"treatment":name,"median_SSE":float(np.median(values))})
        output=self.scratch/"out"
        figures=self.scratch/"fig"
        pilot.create_mos2_style_figures(pd.DataFrame(rows),tables,output,figures,"fixture",100.0,
            seed=1013,bootstrap=64)
        convergence=pd.read_csv(output/"median_bootstrap_convergence.csv")
        baseline=convergence.loc[convergence.treatment=="M0_Baseline"].sort_values("sample_size")
        self.assertEqual(baseline.sample_size.tolist(),[2,3,4])
        self.assertEqual(baseline.median_SSE.tolist(),[1.5,2.0,2.5])
        # Independently reproducible values from NumPy's seeded bootstrap over
        # the documented cumulative prefixes [1,2], [1,2,3], and [1,2,3,4].
        np.testing.assert_allclose(baseline["bootstrap_CI_2.5"].to_numpy(),[1.0,1.0,1.5])
        np.testing.assert_allclose(baseline["bootstrap_CI_97.5"].to_numpy(),[2.0,3.0,4.0])
        self.assertEqual(set(convergence.bootstrap_draws),{64})
        self.assertEqual(set(convergence["round"]),{"fixture"})
        self.assertEqual(Image.open(output/"05_mitigation_median_CI_convergence.png").size,(11400,5400))

        positioning=pd.read_csv(output/"mitigation_positioning_data.csv")
        none=positioning.loc[positioning.treatment=="M0_Baseline"].iloc[0]
        dd=positioning.loc[positioning.treatment=="M1_DD"].iloc[0]
        self.assertEqual(none.median_SSE,2.5)
        self.assertEqual(none.median_SSE_over_reference,1.0)
        self.assertEqual(dd.median_SSE,3.5)
        self.assertAlmostEqual(dd.median_SSE_over_reference,1.4)
        self.assertEqual(set(positioning.reference_type),{"new_qpu_baseline"})

    def test_wide_figure_and_human_readable_treatment_output_contract(self):
        from matplotlib.figure import Figure
        rows=[]
        tables=[]
        for treatment_index,(name,*_) in enumerate(pilot.TREATMENTS):
            values=np.arange(1,5,dtype=float)+treatment_index
            rows.extend({"treatment":name,"pub":index,"SSE":value}
                        for index,value in enumerate(values,1))
            tables.append({"treatment":name,"median_SSE":float(np.median(values))})
        output=self.scratch/"out"
        figures=self.scratch/"fig"
        captured=[];real_savefig=Figure.savefig
        def capture(fig,*args,**kwargs):
            title=fig._suptitle.get_text() if fig._suptitle is not None else fig.axes[0].get_title()
            captured.append((Path(args[0]).name,title,kwargs.get("dpi"),tuple(fig.get_size_inches())))
            return real_savefig(fig,*args,**kwargs)
        with patch.object(Figure,"savefig",new=capture):
            pilot.create_mos2_style_figures(pd.DataFrame(rows),tables,output,figures,"fixture",100.0,
                seed=1013,bootstrap=16,n=4)
        names=("05_mitigation_median_CI_convergence.png",
               "06_mitigation_three_tier_positioning.png")
        for name in names:
            for path in (output/name,figures/f"fixture_{name}"):
                self.assertTrue(path.is_file(),path)
                self.assertGreater(path.stat().st_size,0)
                with Image.open(path) as image:
                    self.assertEqual(image.size,{"05_mitigation_median_CI_convergence.png":(11400,5400),
                        "06_mitigation_three_tier_positioning.png":(9000,3900)}[name])
                    dpi=image.info.get("dpi")
                    self.assertIsNotNone(dpi)
                    expected_dpi=600
                    self.assertAlmostEqual(float(dpi[0]),expected_dpi,delta=1)
                    self.assertAlmostEqual(float(dpi[1]),expected_dpi,delta=1)
        self.assertEqual([pilot.treatment_display(name) for name,*_ in pilot.TREATMENTS],
            ["None","DD","TREX","ZNE","DD+TREX","DD+ZNE","TREX+ZNE","DD+TREX+ZNE"])
        self.assertEqual(captured,[
            ("05_mitigation_median_CI_convergence.png","Median SSE Convergence (n=4)",600,(19.0,9.0)),
            ("06_mitigation_three_tier_positioning.png","Mitigation Positioning (n=4)",600,(15.0,6.5))])
        for name in names:
            with Image.open(output/name) as image:
                self.assertAlmostEqual(image.info["dpi"][0],600,delta=1)
                self.assertAlmostEqual(image.info["dpi"][1],600,delta=1)
        for name in ("median_bootstrap_convergence.csv","mitigation_positioning_data.csv"):
            self.assertTrue((output/name).is_file())
            self.assertTrue((figures/f"fixture_{name}").is_file())

    def test_per_pub_eis_mapping_sse_and_rmse_follow_canonical_model(self):
        sys.dont_write_bytecode=True
        from repository_paths import register_science
        register_science(ROOT)
        from parameter_mapping import expectations_to_parameters
        from impedance_model import impedance_model
        evs=np.array([-1.2,-1.0,-0.5,0.0,0.5,1.0,1.2])
        frequency=np.array([1.0,10.0,100.0])
        zexp=np.array([5.0-1.0j,3.0-2.0j,1.0-0.5j])
        params,zfit,sse,rmse=pilot.calculate_pub_eis(evs,frequency,zexp)
        clipped=np.clip(evs,-1.0,1.0)
        expected_params,_=expectations_to_parameters(clipped)
        expected_z=np.asarray(impedance_model(frequency,**expected_params),dtype=complex)
        expected_sse=float(np.sum(np.abs(expected_z-zexp)**2))
        self.assertEqual(params,expected_params)
        np.testing.assert_allclose(zfit,expected_z,rtol=0,atol=0)
        self.assertAlmostEqual(sse,expected_sse,places=12)
        self.assertAlmostEqual(rmse,float(np.sqrt(expected_sse/(2*len(frequency)))),places=12)

    def test_archived_n5_sse_figures_use_all_validated_per_pub_rows(self):
        # This is the real, hash-verified historical partial-analysis table:
        # 39 finite PUBs, with the invalid M5 PUB retained separately in its
        # original pub_validation.csv and omitted from this validated-only table.
        archive=ROOT/"02_Hardware/01_vqe/06_DD_TREX_ZNE_mitigation/output/02_run_level_data/diagnostics/independent_n_partial/vqe-independent-n-01-n5-target-n5-chunk-5/snapshot_2feab2d1609e2887/per_pub_eis.csv"
        frame=pd.read_csv(archive)
        self.assertEqual(len(frame),39)
        self.assertTrue(np.isfinite(frame.SSE.to_numpy(dtype=float)).all())
        self.assertEqual(int((frame.treatment=="M5_DD_ZNE").sum()),4)
        stats=pilot.sse_plot_statistics(frame)
        self.assertEqual([item["treatment"] for item in stats],[item[0] for item in pilot.TREATMENTS])
        classical=pilot._classical_sse_reference(ROOT)
        self.assertAlmostEqual(classical,120.22451879422375,places=8)
        out=self.scratch/"n5_figure_contract"
        import matplotlib.pyplot as plt
        from matplotlib.axes import Axes
        created=[];errorbars=[]
        real_subplots=plt.subplots;real_errorbar=Axes.errorbar
        def capture_subplots(*args,**kwargs):
            figure,axes=real_subplots(*args,**kwargs);created.append(axes);return figure,axes
        def capture_errorbar(axis,*args,**kwargs):
            errorbars.append(kwargs);return real_errorbar(axis,*args,**kwargs)
        with patch("matplotlib.pyplot.subplots",side_effect=capture_subplots), \
             patch("matplotlib.axes.Axes.errorbar",new=capture_errorbar):
            names=pilot.plot_sse_treatment_figures(frame,out,classical,"PARTIAL HISTORICAL n=5",n=5)
        self.assertEqual(names,["distribution_pilot.png","sse_plot_summary.csv"])
        self.assertEqual(len(created),1)
        self.assertEqual(len(created[0].patches),8)  # one box per ordered M0-M7 treatment
        self.assertEqual(created[0].get_title(),"SSE Distribution (n=5)")
        self.assertEqual(created[0].get_yscale(),"log")
        self.assertEqual(len(errorbars),0)
        reference=[line for line in created[0].lines if np.asarray(line.get_ydata()).shape==(2,)
                   and np.allclose(line.get_ydata(),[classical,classical])]
        self.assertEqual(len(reference),1)
        self.assertTrue(np.allclose(reference[0].get_ydata(),[classical,classical]))
        exported=pd.read_csv(out/"sse_plot_summary.csv")
        for row in exported.itertuples():
            values=frame.loc[frame.treatment==row.treatment,"SSE"].to_numpy(dtype=float)
            self.assertEqual(row.count,len(values))
            self.assertAlmostEqual(row.median_SSE,float(np.median(values)),places=12)
            self.assertAlmostEqual(row.q25_SSE,float(np.percentile(values,25)),places=12)
            self.assertAlmostEqual(row.q75_SSE,float(np.percentile(values,75)),places=12)
            self.assertAlmostEqual(row.median_over_classical_SSE,row.median_SSE/classical,places=12)
        self.assertFalse((out/"barchart_pilot.png").exists())
        for filename in names[:1]:
            with Image.open(out/filename) as image:
                self.assertEqual(image.size,(9000,4500))
                dpi=image.info.get("dpi")
                self.assertIsNotNone(dpi)
                self.assertAlmostEqual(float(dpi[0]),600,delta=1)
                self.assertAlmostEqual(float(dpi[1]),600,delta=1)
        with self.assertRaises(FileExistsError):
            pilot.plot_sse_treatment_figures(frame,out,classical,"must-not-overwrite")

    def test_publication_figures_keep_exact_titles_margins_and_square_600dpi_for_all_targets(self):
        import matplotlib.pyplot as plt
        from matplotlib.figure import Figure
        expected_titles={
            "publication_nyquist.png":"Nyquist",
            "publication_bode_magnitude.png":"Bode Magnitude",
            "publication_bode_phase.png":"Bode Phase",
            "publication_residual_real.png":"Real Residual",
            "publication_residual_imag.png":"Imaginary Residual",
            "publication_residual_real_normalized.png":"Normalized Real Residual",
            "publication_residual_imag_normalized.png":"Normalized Imaginary Residual",
        }
        labels=[name for name,*_ in pilot.TREATMENTS]
        frequency=np.array([1.,10.,100.,1000.])
        zexp=np.array([30-5j,20-10j,10-20j,5-15j])
        curves={"Ideal":zexp*.95}
        for index,name in enumerate(labels):curves[name]=zexp*(1+.01*(index+1))
        summaries=[{"treatment":name,"median_SSE":float(index+1)} for index,name in enumerate(labels)]
        original_savefig=Figure.savefig
        captures=[]
        def capture(fig,*args,**kwargs):
            fig.canvas.draw()
            bbox=fig.get_tightbbox(fig.canvas.get_renderer())
            width,height=fig.get_size_inches()
            self.assertGreaterEqual(bbox.x0,-.025);self.assertGreaterEqual(bbox.y0,-.025)
            self.assertLessEqual(bbox.x1,width+.025);self.assertLessEqual(bbox.y1,height+.025)
            axis=fig.axes[0]
            captures.append((Path(args[0]).name,fig.get_size_inches().tolist(),kwargs,
                axis.get_title(),axis.get_xlabel(),axis.get_ylabel(),axis.get_box_aspect(),
                axis.get_xlim(),[item.get_text() for item in fig.legends[0].get_texts()] if fig.legends else []))
            if "n5" in str(args[0]):return original_savefig(fig,*args,**kwargs)
        for target_n in (5,10,15,20,25,30):
            rows=[{"treatment":name,"pub":index,"SSE":float(index+1)}
                  for name in labels for index in range(target_n)]
            out=self.scratch/f"pub_n{target_n}"
            out.mkdir(parents=True,exist_ok=True)
            before=len(captures)
            with patch.object(Figure,"savefig",new=capture):
                names=pilot.publication_mitigation_figures(out,frequency,zexp,curves,
                    pd.DataFrame(rows),labels,summaries,n=target_n)
            self.assertEqual(set(names),set(expected_titles))
            target_captures=captures[before:]
            self.assertEqual(len(target_captures),7)
            for filename,size,kwargs,title,xlabel,ylabel,box_aspect,xlim,legend_entries in target_captures:
                self.assertEqual(size,[5.2,5.2])
                self.assertEqual(kwargs.get("dpi"),600)
                self.assertEqual(title,f"{expected_titles[filename]} (n={target_n})")
                self.assertNotIn("EIS14",title)
                self.assertTrue(xlabel);self.assertTrue(ylabel)
                self.assertEqual(box_aspect,1)
                if filename=="publication_nyquist.png":
                    self.assertEqual(xlim,(0.0,60.0))
                    self.assertEqual(legend_entries[0],"Experimental")
                    self.assertEqual(len(legend_entries),10)
            plt.close("all")
        for filename in expected_titles:
            with Image.open(self.scratch/"pub_n5"/filename) as image:
                self.assertEqual(image.size,(3120,3120))
                self.assertAlmostEqual(image.info["dpi"][0],600,delta=1)
                self.assertAlmostEqual(image.info["dpi"][1],600,delta=1)

    def test_final_export_inventory_excludes_obsolete_pilot_figures(self):
        sys.path.insert(0,str(CODE.parents[0]/"03_ai_assistant"))
        import scientific_exports
        self.assertEqual(set(scientific_exports.WIDE_FIGURES),{
            "distribution_pilot.png","05_mitigation_median_CI_convergence.png",
            "06_mitigation_three_tier_positioning.png"})
        self.assertFalse({"nyquist_pilot.png","bode_pilot.png","residual_pilot.png"}
                         & set(scientific_exports.WIDE_FIGURES))

    def test_sse_figure_refuses_nonfinite_or_missing_treatment_rows(self):
        archive=ROOT/"02_Hardware/01_vqe/06_DD_TREX_ZNE_mitigation/output/02_run_level_data/diagnostics/independent_n_partial/vqe-independent-n-01-n5-target-n5-chunk-5/snapshot_2feab2d1609e2887/per_pub_eis.csv"
        frame=pd.read_csv(archive)
        frame.loc[frame.index[0],"SSE"]=np.nan
        with self.assertRaisesRegex(ValueError,"refusing to silently drop"):
            pilot.sse_plot_statistics(frame)

    def test_complete_vqe_validation_gate_still_rejects_partial_treatment(self):
        # The adopted n=5 archive has only four valid M5 PUBs, so it cannot
        # enter the COMPLETE-only analyzer/export path.
        with self.assertRaisesRegex(ValueError,"expected 5 PUBs, got 4"):
            pilot._validated_rows([{} for _ in range(4)],5,"M5_DD_ZNE")


if __name__=="__main__":unittest.main()
