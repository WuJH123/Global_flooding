# -*- coding: utf-8 -*-
"""Reassemble existing GF10 Figure1 + Stage73 audit as a two-page evidence plate.

DO NOT replace the original Figure1 with a false multi-source validation claim.
Page 1 is the previously computed observation frame; page 2 is candidate
corroboration computed from actual reference data. No invented data.
"""
import argparse
from pathlib import Path
import json
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--figure1',type=Path,required=True)
    ap.add_argument('--stage73',type=Path,required=True)
    ap.add_argument('--out',type=Path,required=True)
    a=ap.parse_args()
    second=a.stage73/'Figure1_multisource_audit.png'
    qcfile=a.stage73/'73_multisource_qc.json'
    for f in (a.figure1,second,qcfile):
        if not f.is_file():raise FileNotFoundError(f)
    qc=json.loads(qcfile.read_text(encoding='utf-8'))
    if qc['status']!='CANDIDATE_REFERENCE_SCREENING_NOT_VALIDATION':
        raise ValueError('Stage73 QC not the expected candidate-screening output')
    a.out.mkdir(parents=True,exist_ok=True)
    target=a.out/'Figure1_v2_two_page_observation_and_cross_reference.pdf'
    with PdfPages(target) as pdf:
        for i,path in enumerate((a.figure1,second)):
            img=plt.imread(path)
            fig,ax=plt.subplots(figsize=(16,9),layout='constrained')
            ax.imshow(img);ax.axis('off')
            fig.suptitle('Figure 1 | Global observational frame' if i==0 else
                         'Supplementary Fig. 1 | Multi-source candidate screening',
                         fontsize=15,fontweight='bold')
            pdf.savefig(fig,dpi=300,bbox_inches='tight')
            plt.close(fig)
    print('[SAVED]',target)
if __name__=='__main__':main()
