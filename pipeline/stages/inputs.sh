#!/bin/zsh
# inputs.sh SHARE TARGET LOCK COMMON OCLP SET: assemble the guest inputs into SHARE, the folder the builder VM sees over
# virtiofs (use a new SHARE per run: the guest's virtiofs view caches files that change in place).
#   SHARE/pristine      payload.yaa, links.txt, firmlinks_payload (unpack), sys/data lists, firmlinks + root_uuid metadata, tools
#   SHARE/oclp    OCLP source at commit OCLP (git archive of $OCLP_REPO) + the Universal-Binaries.dmg it expects
#   SHARE/kdk     the build's KDK, expanded (System/Library/Extensions + SystemVersion.plist)
#   SHARE/py SHARE/py-x86_64 SHARE/site   arm64 and x86_64 CPython + the site-packages OCLP imports (universal)
#   SHARE/patches.txt   the set's image patches that apply to this target and build (pipeline/patches.py plan)
#   SHARE/optional.json the set's optional patches that ran or were skipped, with the reason
#   SHARE/hooks/NN-<name>  the set's oclp-hook patches (patches/<name>/src, OCLP runs its hook.sh SYS DATA) and
#   SHARE/post/NN-<name>   post patches (patches/<name>), NN = set order; each with inputs/<row> for its lock rows and,
#                          when the patch has make.sh, build/ from `sh make.sh build` on the host (as pipeline/recovery)
#   SHARE/profile.json    the target's OCLP hardware profile (null for a target without one: golden-first only)
set -eu
SHARE=${1:?share}; TARGET=${2:?target json}; LOCK=${3:?inputs lock}; COMMON=${4:?common lock}; OCLP=${5:?OCLP commit}; SET=${6:?set}
HERE=${0:A:h}; TOP=${HERE:h:h}; L=(python3 -I $TOP/pipeline/lock.py)
OCLP_REPO=${OCLP_REPO:-$TOP/store/oclp}
# Every row handed on is copied first and checked as copied (lock.py stage, take, take-tree): never read by its row's
# path after a check, which another process could change in between.
stage() { $L stage $1 $2 $3 >/dev/null; }   # LOCK ROW DEST

FOUNDATION=${7:-legacy}
case $FOUNDATION in legacy|golden-first) ;; *) print -u2 "unknown foundation: $FOUNDATION"; exit 2 ;; esac
mkdir -p $SHARE
# Archives that are extracted or run (KDK, OCLP source, CPython) are copied into V, checked against their rows as they
# are copied, and extracted from there afresh on every run.
V=$(mktemp -d ${SHARE:A:h}/.inputs.XXXXXX); trap 'rm -rf $V' EXIT
take() { $L take $1 $2 $(mktemp -d $V/$2.XXXXXX) ${3:-}; }
KDKDMG=$(take $LOCK kdk)
mkdir -p $SHARE/pristine $SHARE/oclp $SHARE/kdk $SHARE/hooks $SHARE/post
cp -p $HERE/copy-hooks.py $SHARE/copy-hooks.py
if [[ $FOUNDATION == legacy ]]; then
    UNPACK=$V/unpack; LISTS=$V/lists; stage $LOCK unpack $UNPACK; stage $LOCK lists $LISTS
    cp -c $UNPACK/payload.yaa $UNPACK/zip/AssetData/payloadv2/{links.txt,firmlinks_payload} $SHARE/pristine/
    cp -c $LISTS/{sys,data,data-symlinks}.tsv $SHARE/pristine/
    cp $HERE/firmlinks.tsv $SHARE/pristine/meta17.tsv; cp $HERE/rootuuid.tsv $SHARE/pristine/
    for t in autool hmeta fl fl-noent; do stage $COMMON $t $SHARE/pristine/$t; chmod 0755 $SHARE/pristine/$t; done
else
    # Golden-first assembly reads its filesystem and donor artifacts explicitly.
    # Only the generic entitled firmlink tool is shared with pristine assembly.
    stage $COMMON fl $SHARE/pristine/fl; chmod 0755 $SHARE/pristine/fl
fi

# OCLP's source at the assembly's commit: from the repository (git archive), or, on a Mac without git (an Intel Mac
# on macOS 27 has no command-line tools), from the archive the assets folder carries: store/oclp-source/OCLP.tar
# (git archive of that commit) with OCLP.commit holding the full commit.
# A store filled by `macOS27Tool fetch` and `pipeline/place` has it as the oclp_source row instead (the commit is the archive's
# pax comment, as git archive writes it).
# Any archive (OCLP_TAR, store/oclp-source or the row's own) must be the oclp_source row's bytes; the commit text is
# only checked for consistency.
OCLP_TAR=${OCLP_TAR:-$TOP/store/oclp-source/$OCLP.tar}
if [[ ! -d $OCLP_REPO/.git && ! -f $OCLP_REPO/HEAD ]] || ! command -v git >/dev/null 2>&1 || ! git --version >/dev/null 2>&1; then
    if [[ -f $OCLP_TAR ]]; then TAR=$(take $COMMON oclp_source $OCLP_TAR); else TAR=$(take $COMMON oclp_source); fi
    tar -x -C $SHARE/oclp -f $TAR
    if [[ -f $OCLP_TAR && -f ${OCLP_TAR:r}.commit ]]; then OCLP_FULL=$(cat ${OCLP_TAR:r}.commit)
    else OCLP_FULL=$(python3 -I -c 'import sys, tarfile; print(tarfile.open(sys.argv[1]).pax_headers.get("comment", ""))' $TAR)
    fi
    [[ $OCLP_FULL == $OCLP* ]] || { print -u2 "inputs.sh: $TAR is OCLP ${OCLP_FULL:-(no commit)}, not $OCLP"; exit 1; }
else
    git -C $OCLP_REPO archive $OCLP | tar -x -C $SHARE/oclp; OCLP_FULL=$(git -C $OCLP_REPO rev-parse $OCLP)
fi
stage $COMMON psp_$($L psp $SHARE/oclp) $SHARE/oclp/Universal-Binaries.dmg

KDK=$($L kdk-name $KDKDMG)
W=$(mktemp -d $V/kdk-x.XXXXXX); mkdir $W/m
hdiutil attach -readonly -nobrowse -mountpoint $W/m $KDKDMG >/dev/null
pkgutil --expand-full $W/m/KernelDebugKit.pkg $W/x >/dev/null; hdiutil detach $W/m >/dev/null
K=$SHARE/kdk/$KDK; mkdir -p $K/System/Library/CoreServices
ditto $W/x/KDK.pkg/Payload/System/Library/Extensions $K/System/Library/Extensions
cp $W/x/KDK.pkg/Payload/System/Library/CoreServices/SystemVersion.plist $K/System/Library/CoreServices/
rm -rf $W

# Both CPythons: SHARE/py (arm64) and SHARE/py-x86_64; the assembly copies the one for the Mac it runs on to its py/.
for arch dir in arm64 py x86_64 py-x86_64; do
    W=$(mktemp -d $V/py.XXXXXX); A=$(take $COMMON python_$arch)
    tar -xzf $A -C $W
    ditto $W/python $SHARE/$dir; rm -rf $W
done
# the x86_64 one runs in a Recovery (macOS27Tool install from the stick), whose shared cache has no libedit/ncurses/panel
python3 -I $TOP/pipeline/stages/weaken-dylibs.py $SHARE/py-x86_64/bin/python3.12
# the site-packages OCLP runs with: copied into SHARE/site and checked there as a tree (what is checked is what runs)
$L take-tree $COMMON python_site $SHARE/site >/dev/null

# A patch input: the Golden Gate build's row (GOLDEN_LOCK, golden-first only), else the donor build's, else common's.
# Optional patches whose rows are not there, or whose group is in SKIP_OPTIONAL (space-separated), are left out of the
# plan; SHARE/optional.json says which ran and which were skipped, and why. The build is OS/DONOR (26B131/25G342)
# when GOLDEN_LOCK is set, so plan checks each recipe's requires against both.
GOLDEN_LOCK=${GOLDEN_LOCK:-}
skip=(); for g in ${=SKIP_OPTIONAL:-}; do skip+=(--skip-optional $g); done
python3 -I $TOP/pipeline/patches.py plan $SET $TARGET ${GOLDEN_LOCK:+${GOLDEN_LOCK:t}/}${LOCK:t:r} --locks ${GOLDEN_LOCK:+$GOLDEN_LOCK:}$LOCK:$COMMON \
    $skip --record $SHARE/optional.json > $SHARE/plan.txt
awk '$1 != "recovery"' $SHARE/plan.txt > $SHARE/patches.txt; rm $SHARE/plan.txt   # recovery patches: pipeline/recovery
lock_of() { if [[ -n $GOLDEN_LOCK && -f $GOLDEN_LOCK/$1 ]]; then print $GOLDEN_LOCK; elif [[ -f $LOCK/$1 ]]; then print $LOCK; else print $COMMON; fi; }
typeset -A staged   # row -> its checked copy, made once per run (a row used by two patches)
i=0
while read -r stage name ins; do
    i=$((i + 1))
    recipe=$(python3 -I $TOP/pipeline/patches.py path $name)
    if [[ $stage == oclp-hook ]]; then d=$SHARE/hooks/$(printf %02d $i)-$name; ditto $recipe/src $d
    else d=$SHARE/post/$(printf %02d $i)-$name; ditto $recipe $d; fi
    [[ ! -f $recipe/make.sh ]] || sh $recipe/make.sh $d/build   # host build of the patch's binaries, as pipeline/recovery
    mkdir -p $d/inputs
    # Bundle mtimes participate in IconServices identities. Preserve the artifact's
    # metadata when cloning it into the share, so its generated indexes stay valid.
    # A row on the USB drive (KIT_ON_DRIVE, create-installer: the userspace images) is linked, never copied: the
    # stick writer moves it into the kit on that drive.
    # The checked copy of such a row is made on that drive (clones) and linked.
    for n in ${=ins}; do
        if [[ -n ${staged[$n]:-} && -L ${staged[$n]} ]]; then ln -s $(realpath ${staged[$n]}) $d/inputs/$n; continue; fi
        if [[ -n ${staged[$n]:-} ]]; then cp -Rpc ${staged[$n]} $d/inputs/$n; continue; fi
        src=$($L where $(lock_of $n) $n)
        if [[ -n ${KIT_ON_DRIVE:-} && -e $src && $(realpath $src) == ${KIT_ON_DRIVE:-}/* ]]; then
            c=$(mktemp -d $KIT_ON_DRIVE/.checked.XXXXXX)/$n; stage $(lock_of $n) $n $c; ln -s $c $d/inputs/$n
        else stage $(lock_of $n) $n $d/inputs/$n; fi
        staged[$n]=$d/inputs/$n
    done
done < $SHARE/patches.txt
# A target without an OCLP hardware profile (the generic one: no OCLP patch sets) has none to record: null, said so.
# The Tahoe-first runner's OCLP stage needs it (oclp.sh --hardware_profile), so legacy refuses such a target.
if python3 -I -c 'import json, sys; sys.exit("oclp_hardware_profile" not in json.load(open(sys.argv[1])))' $TARGET; then
    $L target $TARGET oclp_hardware_profile > $SHARE/profile.json
elif [[ $FOUNDATION == legacy ]]; then
    print -u2 "inputs.sh: ${TARGET:t} has no oclp_hardware_profile: the Tahoe-first runner's OCLP stage needs one"; exit 1
else
    echo null > $SHARE/profile.json; echo "inputs.sh: ${TARGET:t} has no OCLP hardware profile (profile.json null)"
fi
{
    echo "oclp $OCLP_FULL ($OCLP), PatcherSupportPkg $($L psp $SHARE/oclp)"
    echo "kdk $KDK ($(shasum -a 256 $KDKDMG | cut -c1-16))"
    echo "set ${SET:t:r}: $(python3 -I $TOP/pipeline/patches.py revisions $(cut -d' ' -f2 $SHARE/patches.txt))"
    echo "foundation $FOUNDATION"
} > $SHARE/MANIFEST.txt
cat $SHARE/MANIFEST.txt
