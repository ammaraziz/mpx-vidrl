"""
Monkeypox assembly report
"""

import json
import matplotlib.patches as patches

import pandas
from pathlib import Path
from pyfastx import Fasta
from rich.table import Table
from rich import print as rprint
from dataclasses import dataclass
from typing import Optional, List, Tuple
import numpy as np
from statistics import median, mean
import seaborn as sns
from matplotlib import pyplot as plt
from Bio import GenBank
from Bio import SeqIO
from cyvcf2 import VCF

@dataclass
class SampleFiles:
    assembly: Path
    qc_data: Path
    samtools: Path
    depletion: Path


@dataclass
class SampleQC:
    name: str
    reads: Optional[int]
    qc_reads: Optional[int]
    human_reads: Optional[int]
    aligned_reads: Optional[int]
    coverage: Optional[float]
    mean_depth: Optional[float]
    missing_sites: Optional[int]
    completeness: Optional[float]

    def to_list(self):
        return [
            self.name,
            self.reads,
            self.qc_reads,
            self.human_reads,
            self.aligned_reads,
            self.coverage,
            self.mean_depth,
            self.missing_sites,
            self.completeness
        ]


def get_nanoq_data(file: Path) -> Tuple[int, int]:
    """
    Get nanoq data
    """
    if file is None:
        return 0, 0

    with file.open() as infile:
        nanoq_data = json.load(infile)

    after_filtered = nanoq_data["reads"]
    filtered = nanoq_data["filtered"]

    return after_filtered+filtered, after_filtered


def get_fastp_data(file: Path or None) -> Tuple[int, int]:
    """
    Get fastp data
    """
    if file is None:
        return 0, 0

    with file.open() as infile:
        fastp_data = json.load(infile)

    all_reads = fastp_data["summary"]["before_filtering"]["total_reads"]
    qc_reads = fastp_data["summary"]["after_filtering"]["total_reads"]

    return all_reads, qc_reads  # Illumina PE


def get_host_reads(file: Path or None, ont: bool, ont_ivar: bool) -> int:
    """
    Get mgp-tools deplete data
    """
    if file is None:
        return 0

    with file.open() as infile:
        mgpt_data = json.load(infile)

    reads = mgpt_data["reads"][0]["depleted"]
    if not ont and not ont_ivar:
        reverse_depleted = mgpt_data["reads"][1]["depleted"]
        reads = reads+reverse_depleted

    return reads


def get_samtools_data(file: Path) -> Tuple[int, float, float]:
    """
    Get samtools coverage data
    """
    content = file.open().readlines()[1].strip().split("\t")
    return int(content[3]), round(float(content[5]), 4), round(float(content[6]), 6)  # numreads, coverage, meandepth


def get_consensus_assembly_data(file: Path) -> Tuple[float or None, int]:
    """
    Get consensus sequence and missing site proportion (N) - should only have a single sequence
    """

    seq_data = [seq for seq in Fasta(str(file), uppercase=True, build_index=False)]
    seq = seq_data[0][1]
    ncount = seq.count("N")
    try:
        completeness = round(100 - ((ncount / len(seq))*100), 6)
    except ZeroDivisionError:
        completeness = None

    return completeness, ncount


def create_rich_table(samples: List[SampleQC], title: str, table_output: Path = None):

    df = pandas.DataFrame(
        [sample.to_list() for sample in samples],
        columns=[
            "Sample", "Reads", "QC reads", "Host reads",
            "Alignments", "Coverage", "Mean Depth", "Missing", "Completeness"
        ]
    )

    df = df.sort_values(["Sample", "Completeness", "Coverage", "Mean Depth"])

    table = Table(title=title)
    for cname in df.columns:
        if cname != "Sample":
            justify = "right"
        else:
            justify = "left"
        table.add_column(cname, justify=justify, no_wrap=False)

    for _, row in df.iterrows():
        if row["Completeness"] >= 95.0:
            row_color = "green1"
        elif 90.0 <= row["Completeness"] < 95.0:
            row_color = "yellow1"
        else:
            row_color = "red1"

        field_str = [f"[{row_color}]{s}" for s in row]
        table.add_row(*field_str)

    if table_output is not None:
        df.to_csv(table_output, sep="\t", header=True, index=False)

    return table


def quality_control_consensus(
    results: Path, subdir: str or None = "high_freq", table_output: Path = None, ont: bool = False, ont_ivar: bool = False
) -> Tuple[pandas.DataFrame, Table]:

    """ Create a quality control table from the coverage data and consensus sequences """

    coverage_data = {
        sample.name.replace(".coverage.txt", ""): sample
        for sample in (results / "coverage").glob("*.coverage.txt")
    }
    if ont or ont_ivar:
        qc_data = {
            sample.name.replace(".nanoq.json", ""): sample
            for sample in (results / "quality_control").glob("*.json")
        }
    else:
        qc_data = {
            sample.name.replace(".json", ""): sample
            for sample in (results / "quality_control").glob("*.json")
        }
    depletion_data = {
        sample.name.replace(".json", ""): sample
        for sample in (results / "host_depletion").glob("*.json")
    }

    combined_files = {}
    if subdir is not None:
        consensus_assemblies = results / "consensus" / subdir
    else:
        consensus_assemblies = results / "consensus"

    for assembly in consensus_assemblies.glob("*.consensus.fasta"):
        name = assembly.name.replace(".consensus.fasta", "")
        
        combined_files[name] = SampleFiles(
            assembly=assembly,
            qc_data=qc_data.get(name),
            samtools=coverage_data.get(name),
            depletion=depletion_data.get(name)
        )

    if not combined_files:
        raise ValueError(f"No consensus sequences found in: {consensus_assemblies}")

    samples = []
    for sample, sample_files in combined_files.items():
        if ont or ont_ivar:
            all_reads, qc_reads = get_nanoq_data(sample_files.qc_data)
        else:
            all_reads, qc_reads = get_fastp_data(sample_files.qc_data)

        aligned_reads, coverage, mean_depth = get_samtools_data(sample_files.samtools)
        completeness, missing = get_consensus_assembly_data(sample_files.assembly)
        host_reads = get_host_reads(sample_files.depletion, ont=ont, ont_ivar=ont_ivar)

        qc = SampleQC(
            name=sample,
            reads=all_reads,
            qc_reads=qc_reads,
            human_reads=host_reads,
            aligned_reads=aligned_reads,
            coverage=coverage,
            mean_depth=mean_depth,
            missing_sites=missing,
            completeness=completeness
        )
        samples.append(qc)

    if subdir is not None:
        table_freq_title = "".join([s.capitalize() for s in subdir.split("_")])
    else:
        table_freq_title = "ONT"

    table = create_rich_table(samples, title=f"Monkeypox QC ({table_freq_title})", table_output=table_output)

    rprint(table)

    df = pandas.DataFrame(
        [sample.to_list() for sample in samples],
        columns=["Sample", "Reads", "QC reads", "Host reads", "Alignments", "Coverage", "Mean Depth", "Missing", "Completeness"]
    )

    return df, table


def read_ivar_variants(consensus_directory: Path) -> pandas.DataFrame:
    """
    Read iVar variant outputs
    """
    variant_dfs = []
    for file in consensus_directory.glob("*.variants.tsv"):
        df = pandas.read_csv(file, sep="\t", header=0)
        if df.empty:
            df.loc[0] = [None for _ in df.columns]

        sample_name = file.name.replace(".variants.tsv", "")
        df['SAMPLE'] = [sample_name for _ in df.iterrows()]
        variant_dfs.append(df)

    return pandas.concat(variant_dfs)


def read_medaka_variants(consensus_directory: Path) -> pandas.DataFrame:
    """
    Read Medaka variant outputs
    """
    variant_dfs = []
    for file in consensus_directory.glob("*.pass.vcf.gz"):
        sample_name = file.name.replace(".pass.vcf.gz", "")
        variants = pandas.DataFrame([
            [
                variant.CHROM,
                variant.POS,
                variant.REF,
                variant.ALT[0],
                variant.QUAL,
                variant.FILTER
            ] for variant in VCF(str(file))
        ], columns=[
            'CHROM',
            'POS',
            'REF',
            'ALT',
            'QUAL',
            'FILTER'
        ])
        if variants.empty:
            variants.loc[0] = [None for _ in variants.columns]

        variants['SAMPLE'] = [sample_name for _ in variants.iterrows()]

        variant_dfs.append(variants)
    return pandas.concat(variant_dfs)


def variant_table(
    results: Path,
    subdir: str = None,
    outdir: Path = Path.cwd(),
    ont_artic: bool = False,
    qc_pass: bool = False,
    min_complete: float = 95.0,
    min_depth: float = 50,
    genbank_file: Path = None,
    mask_file: Path = None,
    freq_alpha: float = 0.3,
    variant_pass: bool = True,
    low_freq_depth: str = None
):

    if not ont_artic:
        if subdir is None:
            print("Subdir must be specified.")
            exit(1)

        variant_df = read_ivar_variants(consensus_directory=results / "consensus" / subdir)
    else:
        variant_df = read_medaka_variants(consensus_directory=results / "consensus")

    if qc_pass:
        qc_df, _ = quality_control_consensus(results=results, subdir=subdir)
        qc_df_pass = qc_df[(qc_df["Completeness"] >= min_complete) & (qc_df["Mean Depth"] >= min_depth)]
        variant_df_pass = variant_df[variant_df["SAMPLE"].isin(qc_df_pass["Sample"])].reset_index(drop=True)
    else:
        variant_df_pass = variant_df.copy()

    # Decorate the passing sample variant table with additional information from the Genbank file

    if genbank_file is not None and mask_file is not None:
        decorate_variants(
            variant_table=variant_df_pass,
            genbank_file=genbank_file,
            mask_file=mask_file,
            ont_artic=ont_artic,
            freq_alpha=freq_alpha,
            variant_pass=variant_pass,
            subdir=subdir,
            outdir=outdir,
            low_freq_depth=low_freq_depth
        )
    else:
        print("Non Genbank file (and/or maskign file) specified, skippign variant annotation")


def decorate_variants(
    variant_table: pandas.DataFrame,
    genbank_file: Path,
    mask_file: Path,
    subdir: str = "high_freq",
    ont_artic: bool = False,
    context_size: int = 5,
    debug: bool = False,
    freq_alpha: float = 0.3,
    variant_pass: bool = True,
    low_freq_depth: str = None,
    outdir: Path = Path.cwd()
):

    """
    Decorate the variant table with information from the GenBank reference, including:

        * APOBEC3 target sites and context: code is validated against the program used by
         the authors of the Nature Medicine communication, produces same output and motif
         frequencies per isolate

         --> https://github.com/insapathogenomics/mutation_profile

        *

    """

    if not outdir.exists():
        outdir.mkdir(parents=True, exist_ok=True)

    with open(genbank_file) as handle:
        record = GenBank.read(handle)

    # TODO: This is super important otherwise indices off,
    # TODO: need to recheck with TWIST data!
    variant_table = variant_table.reset_index()

    apobec3_data = []
    ns_data = []
    for _, row in variant_table.iterrows():
        pos = row["POS"]

        if pos is None:
            ns_data.append([None])
            apobec3_data.append([None, None, None])
            continue

        pos_seq = pos-1  # 0-indexed sequence, 1-indexed POS

        apobec3 = False
        pattern = "other"
        if row["REF"] == "C" and row["ALT"] == "T":
            if record.sequence[pos_seq-1].capitalize() == "T":
                apobec3 = True
                pattern = "TC>TT"
        if row["REF"] == "G" and row["ALT"] == "A":
            if record.sequence[pos_seq+1].capitalize() == "A":
                apobec3 = True
                pattern = "GA>AA"

        if apobec3:
            var_context, var_context_display = get_context_sequence(record, row["ALT"], pos_seq, context_size)
        else:
            var_context, var_context_display = "", ""

        if debug:
            rprint(
                f"Variant [{row['SAMPLE']}: {pos}] context - REF: {row['REF']} ALT: {row['ALT']}"
                f" - CONTEXT: {var_context} - APOBEC3: {apobec3}"
            )

        try:
            if row["ALT_AA"] is np.nan:
                ns_data.append([False])
            else:
                ns_data.append([row["REF_AA"] != row["ALT_AA"]])
        except KeyError:
            ns_data.append([None])

        apobec3_data.append([apobec3, pattern, var_context])

    # Annotate

    apobec_df = pandas.DataFrame(
        apobec3_data, columns=["APOBEC3", "PATTERN", "CONTEXT"]
    )
    ns_df = pandas.DataFrame(
        ns_data, columns=["NS"]
    )

    print(apobec_df)

    variants_apobec = variant_table.join(apobec_df)
    variants_ns = variants_apobec.join(ns_df)

    variants_masked, mask_df = annotate_masked_regions(variants_ns, mask_file)
    variants_cds = annotate_cds(variants_masked, genbank_file)

    # Following require iVar output:

    if not ont_artic:
        if variant_pass:
            variants_cds = variants_cds[variants_cds["PASS"] == True]

        if low_freq_depth:
            settings = [setting.split(":") for setting in low_freq_depth.split("-")]
            for (freq, min_depth) in sorted(settings, key=lambda x: x[0]):
                # Sort by frequency thresholds, then apply the minimum depth criterion
                # this will drop variants successively e.g. first those <= 1% and < 1000x
                # then <= 5% and 300x, then <= 10% and 100x (Nature Medicine microevolution)
                var_cds = variants_cds.copy()
                for idx, variant in var_cds.iterrows():
                    if variant["ALT_FREQ"] <= float(freq) and variant["ALT_DP"] < int(min_depth):
                        if debug:
                            print(f"FAIL variant : {variant['ALT_FREQ']} with {variant['ALT_DP']}")
                        variants_cds.drop(idx, inplace=True)
                variants_cds.reset_index(drop=True, inplace=True)

    if debug:
        print("APOBEC3")
        print(apobec_df)
        print("NS")
        print(ns_df)
        print("BASE")
        print(variant_table)
        print("APOBEC MERGE")
        print(variants_apobec)
        print("NS MERGE")
        print(variants_ns)
        print("MASK MERGE")
        print(variants_cds)
        print(variants_cds[variants_cds["MASK"] == True])

    mask_cds_df = get_mask_cds(genbank_file=genbank_file, mask_df=mask_df)

    mask_name = mask_file.name.split(".")[-2]
    gbk_name = genbank_file.name.split(".")[-2]

    if subdir is not None:
        _subdir = f"_{subdir}_"
    else:
        _subdir = f"_"

    if not ont_artic:
        plot_variant_distribution(
            df=variants_cds,
            ref_length=len(record.sequence),
            output_file=outdir / f"variant_distr{_subdir}{mask_name}_{gbk_name}.png",
            mask_df=mask_df,
            freq_alpha=freq_alpha
        )

        plot_apobec_frequencies(
            df=variants_cds,
            output_file=outdir / f"apobec_sample{_subdir}{gbk_name}.png"
        )
        plot_non_synonymous(
            df=variants_cds,
            output_file=outdir / f"ns_sample{_subdir}{gbk_name}.png"
        )

    variant_summary = get_variant_pop_summary(variants=variants_cds, ont_artic=ont_artic)
    variant_summary.to_csv(outdir / f"variants_summary{_subdir}{mask_name}_{gbk_name}.tsv", sep="\t", index=False)

    variants_cds.to_csv(outdir / f"variants{_subdir}{mask_name}_{gbk_name}.tsv", sep="\t", index=False)
    mask_cds_df.to_csv(outdir / f"mask_cds{_subdir}{mask_name}_{gbk_name}.tsv", sep="\t", index=False)

    return variants_cds


def get_variant_pop_summary(variants: pandas.DataFrame, ont_artic: bool):

    samples = len(variants["SAMPLE"].unique())
    var_data = []
    for _, data in variants.groupby(["POS", "REF", "ALT"]):
        rep = data.iloc[0]
        if not ont_artic:
            rep = rep.drop(
                labels=[
                    "REF_DP", "REF_RV", "REF_QUAL", "ALT_DP", "ALT_RV", "ALT_QUAL", "ALT_FREQ",
                    "TOTAL_DP", "PASS", "PVAL", "SAMPLE"
                ]
            )
        rep["POP_COUNT"] = len(data)
        rep["POP_FREQ"] = len(data)/samples
        var_data.append(rep)

    var_data = pandas.DataFrame(var_data)
    cols_no_pop = var_data.columns.tolist()[:-2]
    cols_no_pop.insert(1, "POP_COUNT")
    cols_no_pop.insert(2, "POP_FREQ")
    var_data = var_data.reindex(columns=cols_no_pop)
    return var_data


def annotate_masked_regions(variants: pandas.DataFrame, file: Path):
    """
    Annotate variants with the masked regions from 1-indexed reference file
    """
    mask = pandas.read_csv(file, sep="\t", header=0)

    mask_data = []
    for _, variant in variants.iterrows():
        masked = False
        annotation = None
        source = None
        if variant["POS"] is not None:  # otherwise no variants called
            for _, region in mask.iterrows():
                start, end = region['start'], region['end']
                mask_range = range(start, end + 1)
                if int(variant["POS"]) in mask_range:
                    masked = True
                    annotation = region['annotation']
                    source = region['source']
        mask_data.append([masked, annotation, source])

    mask_df = pandas.DataFrame(mask_data, columns=["MASK", "MASK_TYPE", "MASK_SOURCE"])
    variants_masked = variants.join(mask_df)

    return variants_masked, mask


def read_cds_features(genbank_file: Path):

    cds = []
    for rec in SeqIO.parse(genbank_file, "genbank"):
        if rec.features:
            for feature in rec.features:
                if feature.type == "CDS":
                    cds.append(feature)
    return cds


def get_mask_cds(genbank_file: Path, mask_df: pandas.DataFrame):

    cds_features = read_cds_features(genbank_file)
    mask_data = []
    for _, region in mask_df.iterrows():
        start, end = region['start'], region['end']
        mask_range = range(start, end+1)

        mask_features = []
        for feature in cds_features:
            if feature.location._start.position in mask_range or feature.location._end.position in mask_range:
                gene, locus_tag, protein_id, product, note = extract_feature_qualifiers(feature=feature)
                mask_features.append(region.to_list() + [gene, locus_tag, protein_id, product, note])
        mask_data += mask_features

    mask_feature_df = pandas.DataFrame(
        mask_data, columns=[
            "chrom", "start", "end", "annotation", "source",
            "GBK_GENE", "GBK_LOCUS_TAG", "GBK_PROTEIN_ID", "GBK_PRODUCT", "GBK_NOTE"
        ]
    )

    return mask_feature_df


def annotate_cds(variants: pandas.DataFrame, genbank_file: Path):

    cds_features = read_cds_features(genbank_file)
    cds_data = []

    for _, variant in variants.iterrows():
        if variant['POS'] is None:
            # No variants called (e.g. US-CDC some isolates from Australia)
            cds_data.append([None, None, None, None, None, None])
            continue

        seq_pos = variant['POS']-1

        intergenic = True
        gene = None
        locus_tag = None
        protein_id = None
        product = None
        note = None

        for feat in cds_features:
            if seq_pos in feat:
                intergenic = False
                gene, locus_tag, protein_id, product, note = extract_feature_qualifiers(feature=feat)

        cds_data.append([intergenic, gene, locus_tag, protein_id, product, note])

    cds_df = pandas.DataFrame(
        cds_data, columns=["INTERGENIC", "GBK_GENE", "GBK_LOCUS_TAG", "GBK_PROTEIN_ID", "GBK_PRODUCT", "GBK_NOTE"]
    )

    print(variants)
    print(cds_df)

    variants_cds = variants.join(cds_df)

    return variants_cds


def extract_feature_qualifiers(feature):

    gene = feature.qualifiers.get("gene")
    if gene is not None:
        gene = ";".join(gene)
    locus_tag = feature.qualifiers.get("locus_tag")
    if locus_tag is not None:
        locus_tag = ";".join(locus_tag)
    protein_id = feature.qualifiers.get("protein_id")
    if protein_id is not None:
        protein_id = ";".join(protein_id)
    product = feature.qualifiers.get("product")
    if product is not None:
        product = ";".join(product)
    note = feature.qualifiers.get("note")
    if note is not None:
        note = ";".join(note)

    return gene, locus_tag, protein_id, product, note


def plot_variant_distribution(df: pandas.DataFrame, output_file: Path, ref_length: int, mask_df: pandas.DataFrame, freq_alpha: float = 0.3):
    """
    Create the plot and save to file
    """
    fig, axes = plt.subplots(
        nrows=2, ncols=1, figsize=(24, 14)
    )
    sns.set_style('white')
    p1 = sns.scatterplot(
        data=df, x="POS", y="ALT_FREQ", hue="APOBEC3", ax=axes[0], palette={True: '#A092B7', False: '#51806a'}, alpha=freq_alpha, s=50
    )
    p1.set_xlim([1, ref_length])
    p1.set_xticks(range(0, ref_length+1, 5000))
    p2 = sns.scatterplot(
        data=df, x="POS", y="ALT_FREQ", hue="NS", ax=axes[1], palette={True: '#A092B7', False: '#51806a'}, alpha=freq_alpha, s=50
    )
    p2.set_xlim([1, ref_length])
    p2.set_xticks(range(0, ref_length + 1, 5000))

    for _, mask_region in mask_df.iterrows():
        start, end = int(mask_region['start']), int(mask_region['end'])
        width = end - start
        rect1 = patches.Rectangle((start, 0), width, 1.01, linewidth=0.1, edgecolor=None, facecolor='gray', alpha=0.3)
        rect2 = patches.Rectangle((start, 0), width, 1.01, linewidth=0.1, edgecolor=None, alpha=0.3, facecolor='gray')
        p1.add_patch(rect1)

        p2.add_patch(rect2)

    plt.tight_layout()
    fig.savefig(output_file)


def plot_apobec_frequencies(df: pandas.DataFrame, output_file: Path):

    """
    Plot putative APOBEC3 frequencies across samples
    """

    pattern_count_data = []
    pattern_freq_data = []
    for sample, sample_df in df[["SAMPLE", "PATTERN"]].groupby(["SAMPLE"]):
        pattern_counts = sample_df.groupby("PATTERN").count()["SAMPLE"]
        pattern_counts.name = sample
        pattern_counts.index.name = "MOTIF"

        try:
            ga = pattern_counts["GA>AA"]
        except KeyError:
            ga = 0

        try:
            tc = pattern_counts["TC>TT"]
        except KeyError:
            tc = 0

        try:
            other = pattern_counts["other"]
        except KeyError:
            other = 0

        if ga == 0 and tc == 0:
            apobec_frac = 0
            other_frac = 0
        else:
            total_variants = ga+tc+other
            apobec_sum = ga+tc
            apobec_frac = round((apobec_sum / total_variants) * 100, 4)
            other_frac = 100 - apobec_frac

        pattern_freq_data.append([sample, apobec_frac, other_frac])
        pattern_count_data.append(pattern_counts)

    dfp = pandas.DataFrame(pattern_count_data)
    dff = pandas.DataFrame(
        pattern_freq_data,
        columns=["Sample", "Putative APOBEC3 target sites", "Other"]
    ).set_index("Sample")

    print("APOBEC mean freq", mean(dff["Putative APOBEC3 target sites"]))

    fig, axes = plt.subplots(
        nrows=2, ncols=1, figsize=(24, 14)
    )
    p1 = dfp.plot(kind='bar', stacked=True, color=['#A092B7', '#4d5f8e', '#51806a'], ax=axes[0])
    p1.set_xlabel("Samples")
    p1.set_ylabel("Pattern counts")

    p2 = dff.plot(kind='bar', stacked=True, color=['#A092B7', '#51806a'], ax=axes[1])
    p2.set_xlabel("Samples")
    p2.set_ylabel("Pattern frequency")

    plt.tight_layout()
    fig.savefig(output_file)


def plot_non_synonymous(df: pandas.DataFrame, output_file: Path):

    ns_count_data = []
    for sample, sample_df in df[["SAMPLE", "NS"]].groupby(["SAMPLE"]):
        ns_counts = sample_df.groupby("NS").count()["SAMPLE"]
        ns_counts.name = sample
        ns_counts.index.name = "Non-synonymous mutation"
        ns_count_data.append((ns_counts/ns_counts.sum())*100)

    dfp = pandas.DataFrame(ns_count_data)

    fig, ax = plt.subplots(
        nrows=1, ncols=1, figsize=(24, 14)
    )
    p1 = dfp.plot(kind='bar', stacked=True, color=['#A092B7', '#51806a'], ax=ax)
    p1.set_xlabel("Samples")
    p1.set_ylabel("Mutation effect frequency")

    plt.tight_layout()
    fig.savefig(output_file)


def get_context_sequence(record, alt: str, vloc_seq: int, context_size: int, ):

    """
    Get the context sequence to print to console for checks
    """

    if vloc_seq - context_size < 0:
        context_start = 0
    else:
        context_start = vloc_seq - context_size

    if vloc_seq + context_size + 1 > len(record.sequence)-1:
        context_end = len(record.sequence)-1
    else:
        context_end = vloc_seq + context_size + 1

    var_context_display = record.sequence[context_start:vloc_seq] + \
        f'[red]{alt}[/red]' + \
        record.sequence[vloc_seq+1:context_end]

    var_context = record.sequence[context_start:vloc_seq] + alt + record.sequence[vloc_seq+1:context_end]

    return var_context, var_context_display
