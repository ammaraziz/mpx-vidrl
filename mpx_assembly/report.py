"""
Monkeypox assembly report
"""

from cmath import nan
import json
from multiprocessing.sharedctypes import Value
import numpy

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
from Bio.Seq import Seq


@dataclass
class SampleFiles:
    assembly: Path
    fastp: Path
    samtools: Path


@dataclass
class SampleQC:
    name: str
    reads: Optional[int]
    qc_reads: Optional[int]
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
            self.aligned_reads,
            self.coverage,
            self.mean_depth,
            self.missing_sites,
            self.completeness
        ]


def get_fastp_data(file: Path) -> Tuple[int, int]:
    """
    Get fastp data - divive by two for paired-end reads
    """
    with file.open() as infile:
        fastp_data = json.load(infile)

    all_reads = fastp_data["summary"]["before_filtering"]["total_reads"]
    qc_reads = fastp_data["summary"]["after_filtering"]["total_reads"]

    return all_reads, qc_reads  # Illumina PE


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


def create_rich_table(samples: List[SampleQC], title: str, patient_id: bool = True, table_output: Path = None):

    df = pandas.DataFrame(
        [sample.to_list() for sample in samples],
        columns=["Sample", "Reads", "QC Reads", "Alignments", "Coverage", "Mean Depth", "Missing", "Completeness"]
    )

    if not patient_id:
        df = df.sort_values(["Sample", "Completeness", "Coverage", "Mean Depth"])
    else:
        # Sort first by sample patient identifier then number of that patient sample
        # must comply with Mona's format: ID_{Patient}_{Number} e.g. MPX_A_1 and MPX_A_2
        patient_samples = {}
        for i, row in df.iterrows():
            sample_id = row["Sample"]
            sample_content = sample_id.split("_")
            patient_id = sample_content[1]
            sample_number = sample_content[2]

            if patient_id not in patient_samples.keys():
                patient_samples[patient_id] = [(int(sample_number), row.tolist())]
            else:
                patient_samples[patient_id].append((int(sample_number), row.tolist()))

        sorted_patient_samples = {}
        for patient_id, patient_data in patient_samples.items():
            sorted_patient_samples[patient_id] = sorted(patient_data, key=lambda x: x[0])

        sorted_samples = dict(sorted(sorted_patient_samples.items()))  # Python 3.7+

        df = pandas.DataFrame(
            [sample[1] for _, data in sorted_samples.items() for sample in data],
            columns=[
                "Sample",
                "Reads",
                "QC Reads",
                "Alignments",
                "Coverage",
                "Mean Depth",
                "Missing (N)",
                "Completeness"
            ]
        )

    table = Table(title=title)
    for cname in df.columns:
        if cname != "Sample":
            justify = "right"
        else:
            justify = "left"
        table.add_column(cname, justify=justify, no_wrap=False)

    for _, row in df.iterrows():
        if row["Completeness"] >= 99.9:
            row_color = "green1"
        elif 95.0 <= row["Completeness"] < 99.9:
            row_color = "pale_green1"
        elif 90.0 <= row["Completeness"] < 95.0:
            row_color = "yellow1"
        else:
            row_color = "red1"

        field_str = [f"[{row_color}]{s}" for s in row]
        table.add_row(*field_str)

    if table_output is not None:
        df.to_csv(table_output, sep="\t", header=True, index=False)

    return table


def quality_control_consensus(results: Path, subdir: str = "high_freq", table_output: Path = None) -> Tuple[pandas.DataFrame, Table]:

    """ Create a quality control table from the coverage data and consensus sequences """

    coverage_data = {
        sample.name.replace(".coverage.txt", ""): sample
        for sample in (results / "coverage").glob("*.coverage.txt")
    }
    fastp_data = {
        sample.name.replace(".json", ""): sample
        for sample in (results / "quality_control").glob("*.json")
    }

    combined_files = {}
    consensus_assemblies = results / "consensus" / subdir
    for assembly in consensus_assemblies.glob("*.consensus.fasta"):
        name = assembly.name.replace(".consensus.fasta", "")
        
        combined_files[name] = SampleFiles(
            assembly=assembly,
            fastp=fastp_data.get(name),
            samtools=coverage_data.get(name),

        )

    if not combined_files:
        raise ValueError(f"No consensus sequences found in: {consensus_assemblies}")

    samples = []
    for sample, sample_files in combined_files.items():
        all_reads, qc_reads = get_fastp_data(sample_files.fastp)
        aligned_reads, coverage, mean_depth = get_samtools_data(sample_files.samtools)
        completeness, missing = get_consensus_assembly_data(sample_files.assembly)

        qc = SampleQC(
            name=sample,
            reads=all_reads,
            qc_reads=qc_reads,
            aligned_reads=aligned_reads,
            coverage=coverage,
            mean_depth=mean_depth,
            missing_sites=missing,
            completeness=completeness
        )
        samples.append(qc)

    table_freq_title = "".join([s.capitalize() for s in subdir.split("_")])
    table = create_rich_table(samples, title=f"Monkeypox QC ({table_freq_title})", table_output=table_output)

    rprint(table)

    df = pandas.DataFrame(
        [sample.to_list() for sample in samples],
        columns=["Sample", "Reads", "QC Reads", "Alignments", "Coverage", "Mean Depth", "Missing", "Completeness"]
    )

    return df, table


@dataclass
class SampleDistance:
    patient: str
    samples: int
    within_median: float


def snp_distance(dist: Path):
    """
    Compute median SNP distance within and between patients
    Sample identifiers conform to Mona's scheme: MPX_A_1 etc.
    """

    dist_mat = pandas.read_csv(dist, index_col=0)

    # Replace column and index names with extracted patient identifier

    patients = [c.split(".")[0].replace("Consensus_", "").split("_")[1] for c in dist_mat.columns]

    dist_mat.index = patients
    dist_mat.columns = patients

    dist_upper = dist_mat.mask(np.triu(np.ones(dist_mat.shape, dtype=np.bool_)))

    melted = pandas.DataFrame(dist_upper).reset_index().melt('index')

    within_patients = melted[melted['index'] == melted['variable']].dropna()
    between_patients = melted[melted['index'] != melted['variable']].dropna()

    # Within patient with only single isolate comparison to itself is already NaN and excluded

    df = pandas.concat([within_patients, between_patients])
    df['comparison'] = ['within' for _ in within_patients.iterrows()] + ['between' for _ in between_patients.iterrows()]\
    
    df.columns = ['patient1', 'patient2', 'distance', 'comparison']

    fig, ax = plt.subplots(
        nrows=1, ncols=1, figsize=(14, 10)
    )
    
    sns.set_style('white')

    p = sns.boxplot(x="distance", y="comparison", data=df, palette="colorblind", linewidth=2.5, ax=ax)
    sns.stripplot(x="distance", y="comparison", data=df, color="darkgray", alpha=0.8, jitter=0.3, size=8, ax=ax)

    p.set_xticks(range(int(df['distance'].max())+1))
    p.set_xticklabels(range(int(df['distance'].max())+1))
    plt.xlabel(f"\nSNP distance", fontsize=12, fontweight="bold")
    plt.ylabel(f"Patient isolates\n", fontsize=12, fontweight="bold")
    plt.tight_layout()
    
    fig.savefig("test.png")


@dataclass
class SubcladeAllele:
    position: int
    alt: str
    clade: str


def variant_table(
    results: Path,
    subdir: str,
    min_complete: float = 95.0,
    min_depth: float = 50,
    genbank_file: Path = None
):

    consensus_directory = results / "consensus" / subdir

    variant_dfs = []
    for file in consensus_directory.glob("*.variants.tsv"):
        df = pandas.read_csv(file, sep="\t", header=0)
        if df.empty:
            df.loc[0] = [None for _ in df.columns]

        sample_name = file.name.replace(".variants.tsv", "")
        df['SAMPLE'] = [sample_name for _ in df.iterrows()]
        variant_dfs.append(df)

    variant_df = pandas.concat(variant_dfs)
    qc_df, _ = quality_control_consensus(results=results, subdir=subdir)

    qc_df_pass = qc_df[(qc_df["Completeness"] >= min_complete) & (qc_df["Mean Depth"] >= min_depth)]

    variant_df_pass = variant_df[variant_df["SAMPLE"].isin(qc_df_pass["Sample"])].reset_index()

    # Decorate the passing sample variant table with additional information from the Genbank file

    if genbank_file is not None:
        variant_df_pass = decorate_variants(variant_table=variant_df_pass, genbank_file=genbank_file)


def decorate_variants(
    variant_table: pandas.DataFrame,
    genbank_file: Path,
    mask_file: Path = None,
    context_size: int = 5,
    debug: bool = False
):

    """
    Decorate the variant table with information from the GenBank reference, including:
        * APOBEC3 target sites and context: code is validated against the program used by
         the authors of the Nature Medicine communication

         --> https://github.com/insapathogenomics/mutation_profile


    """

    with open(genbank_file) as handle:
        record = GenBank.read(handle)

    apobec3_data = []
    ns_data = []
    for _, row in variant_table.iterrows():
        sample = row["SAMPLE"]

        pos = row["POS"]
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

        var_context, var_context_display = get_context_sequence(record, row["ALT"], pos_seq, context_size)

        if debug:
            rprint(
                f"Variant [{row['SAMPLE']}: {pos}] context - REF: {row['REF']} ALT: {row['ALT']}"
                f" - CONTEXT: {var_context} - APOBEC3: {apobec3}"
            )

        ns_data.append([sample, pos, row["REF_AA"] == row["ALT_AA"]])
        apobec3_data.append([sample, pos, apobec3, pattern, var_context])

    apobec_df = pandas.DataFrame(
        apobec3_data, columns=["SAMPLE", "POS", "APOBEC3", "PATTERN", "CONTEXT"]
    )
    ns_df = pandas.DataFrame(
        ns_data, columns=["SAMPLE", "POS", "NS"]
    )

    variants_apobec = pandas.merge(
        variant_table, apobec_df, how='left', left_on=['POS', 'SAMPLE'], right_on=['POS', 'SAMPLE']
    )

    variants_ns = pandas.merge(
        variants_apobec, ns_df, how='left', left_on=['POS', 'SAMPLE'], right_on=['POS', 'SAMPLE']
    )

    plot_variant_frequencies(variants=variants_ns, ref_length=len(record.sequence))

    return variant_table


def plot_variant_frequencies(variants: pandas.DataFrame, ref_length: int):
    """
    Create a plot of variant occurrence counts along their positions on the reference
    """

    fig, axes = plt.subplots(
        nrows=2, ncols=1, figsize=(24, 14)
    )

    sns.set_style('white')

    p1 = sns.scatterplot(data=variants, x="POS", y="ALT_FREQ", hue="PATTERN", ax=axes[0])
    p1.set_xlim([1, ref_length])

    p2 = sns.scatterplot(data=variants, x="POS", y="ALT_FREQ", hue="NS", ax=axes[1])
    p2.set_xlim([1, ref_length])

    plt.tight_layout()
    fig.savefig("variant_freqs_all.png")

    for sample, sample_df in variants.groupby("SAMPLE"):
        fig, axes = plt.subplots(
            nrows=2, ncols=1, figsize=(24, 14)
        )

        sns.set_style('white')

        p1 = sns.scatterplot(data=sample_df, x="POS", y="ALT_FREQ", hue="PATTERN", ax=axes[0])
        p1.set_xlim([1, ref_length])

        p2 = sns.scatterplot(data=sample_df, x="POS", y="ALT_FREQ", hue="NS", ax=axes[1])
        p2.set_xlim([1, ref_length])

        plt.tight_layout()
        fig.savefig(f"variant_freqs_{sample}.png")


def plot_apobec_frequencies(df: pandas.DataFrame):

    """
    Plot putative APOBEC3 frequencies across samples
    """

    pass


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

    
def get_pattern_frequency(df: pandas.DataFrame) -> dict:

    sample_freqs = {}
    for sample, sample_df in df.groupby("SAMPLE"):
        variants = len(sample_df)
        pattern_frequency = {}
        for pattern, pattern_df in sample_df.groupby("PATTERN"):
            patterns = len(pattern_df)
            pattern_frequency[pattern] = round(
                (patterns/variants)*100, 4
            )
        pattern_frequency["total_apobec"] = 100 - pattern_frequency['other']
        pattern_frequency["total"] = variants
        sample_freqs[sample] = pattern_frequency

    return sample_freqs
