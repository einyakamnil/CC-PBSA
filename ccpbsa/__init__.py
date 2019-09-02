from .CCPBSA import *
import argparse
import os
import sys
from multiprocessing import Pool

pkgpath = __path__[0]
cliparser = argparse.ArgumentParser(prog='ccpbsa')

cliparser.add_argument(
    "routine",
    help="The first argument chooses which routine to run",
    choices={'setup', 'stability', 'affinity', 'gxg'}
)

options = cliparser.add_argument_group("OPTIONS")

options.add_argument(
    "-w", "--wildtype",
    help=".pdb file of the wildtype protein."
)
options.add_argument(
    "-m", "--mutations",
    help="a .txt file with the list of mutations. Each mutant separated \
    by a newline. Multiple mutations in the same mutant are to be \
    separated by comma."
)
options.add_argument(
    "-f", "--flags",
    help="The flags, which should be passed to CONCOORD and GROMACS. At \
    least the number of structures in CONCOORD and the forcefield and water \
    model should be specified in there",
    default=pkgpath+'/parameters/flags.txt'
)
options.add_argument(
    "-c", "--chains",
    help="Name of chains in the .pdb file for the first protein group. \
    Only needed in affinity calculation.",
    default='A',
    nargs='+'
)
options.add_argument(
    "--fit-parameters",
    help="scaling factors of the ddG calculations. Names should fit the \
    kind of calculation. Default parameters depend on what is calculated",
    default=pkgpath+'',
)
options.add_argument(
    "--energy-mdp",
    default=pkgpath+'/parameters/energy.mdp',
    help=".mdp file for GROMACS Lennard-Jones Energy evaluations."
)
options.add_argument(
    "--gxg-table",
    default=pkgpath+'/parameters/GXG.csv',
    help="GXG table used for stability change calculations."
)
options.add_argument(
    "-v",
    help="Print stdout and stderr of the programs.",
    action='store_true'
)
options.add_argument(
    "--no-concoord",
    help="Run energy extraction from minimized structures only, without \
    generating structure ensembles with CONCOORD.",
    action='store_true'
)
options.add_argument(
    '--cores',
    default=0,
    help="Specify the number of cores to use for multiprocessing, takes \
    the maximum amount available per default.",
    type=int
)

cliargs = cliparser.parse_args()
gxg_table = os.path.abspath(cliargs.gxg_table)

if cliargs.v:
    verbose = 1
else:
    verbose = 0

if cliargs.cores == 0:
    cliargs.cores = os.cpu_count()

pool = Pool(cliargs.cores)

def main():
    """Commandline interface for ccpbsa. Handles multiprocessing and argument
    parsing
    """
    if cliargs.routine == 'setup':

        with open(pkgpath+'/parameters/flags.txt', 'a') as paramfile:
            paramfile.write("-tablep="+pkgpath+'/parameters/table4r-6-12.xvg\n')
            paramfile.write("-table="+pkgpath+'/parameters/table4r-6-12.xvg\n')
            paramfile.write("[grompp]\n")
            paramfile.write("-maxwarn=2\n")
            paramfile.write("-f="+pkgpath+'/parameters/min.mdp\n')
            paramfile.write("[gropbe]\n")
            paramfile.write(pkgpath+'/parameters/gropbe.txt\n')
            sys.exit(0)


    if cliargs.routine == 'gxg':
        print("Making a new GXG table.")
        print("Initializing directory.")
        gxg = GXG(
            cliargs.flags,
            cliargs.energy_mdp,
            verbose
        )
        G_table, G_sgl = gxg.create_table()
        print(G_table)
        G_table.to_csv('GXG.csv')
        G_sgl.to_csv('GXG_all_values.csv')

    if cliargs.routine == 'stability':

        print("Initializing directory.")
        data = DataGenerator(
            wtpdb = cliargs.wildtype,
            mutlist = cliargs.mutations,
            flags = cliargs.flags,
            spmdp = cliargs.energy_mdp,
            verbosity = verbose
        )


        if cliargs.no_concoord:

            return data

        else:

            data.types += ['concoord']

            return data

            def multimini(d):
                os.chdir(d)

                try:
                    data.do_minimization(d)

                except KeyError:
                    raise Exception("Missing flags for GROMACS, check flags file")

                os.chdir(data.maindir)

            def multicoord(d):
                os.chdir(d)

                try:
                    data.do_concoord(d)
                    
                except FileNotFoundError:
                    raise Exception("Your CONCOORD run failed!") 

                os.chdir(data.maindir)


            def multropy(en):
                os.chdir(en)
                data.schlitter(en)
                os.chdir(data.maindir)

            pool.map(multimini, data.wds)
            pool.close()
            pool.join()

            data.update_structs()

            pool.map(multicoord, data.wds)
            pool.close()
            pool.join()

            ensembles = data.wds.copy()
            data.wds = [d+'/'+str(i) for d in data.wds \
                    for i in range(1, len(data)+1)]

            pool.map(multienergy, data.wds)
            pool.close()
            pool.join()

            pool.map(multropy, data.wds)
            pool.close()
            pool.join()


    elif cliargs.routine == 'affinity':

        if cliargs.fit_parameters == pkgpath:
            fitprm = pkgpath + '/parameters/fit_affinity.txt'
            cliargs.fit_parameters = fitprm

        with open(cliargs.fit_parameters, 'r') as fit:
            parameters = fit.readlines()
            parameters = [l[:-1] for l in parameters] # Remove newlines
            parameters = [l.split("=") for l in parameters]
            parameters = dict([(l[0], float(l[1])) for l in parameters])

        print("Initializing directory.")

        data = AffinityGenerator(
            wtpdb = cliargs.wildtype,
            mutlist = cliargs.mutations,
            flags = cliargs.flags,
            chaingrp = "".join(cliargs.chains),
            spmdp = cliargs.energy_mdp,
            verbosity = verbose
        )

        def multienergy(d):
            os.chdir(d)
            data.do_minimization(d)
            data.single_point()
            data.electrostatics()
            data.lj()
            data.area()
            os.chdir(data.maindir)

        def multienergy_chains(d):
            os.chdir(d)
            data.split_chains(d)
            data.do_minimization_chains()
            data.single_point_chains()
            data.electrostatics_chains()
            data.lj_chains()
            os.chdir(datdataindir)

        if cliargs.no_concoord:
            case = 3
            pool.map(multienergy, data.wds)
            pool.close()
            pool.join()

            pool.map(multienergy_chains, data.wds)
            pool.close()
            pool.join()

            data.area()

        else:
            case = 4

            def multimini(d):
                os.chdir(d)

                try:
                    data.do_minimization(d)

                except KeyError:
                    raise Exception("Missing flags for GROMACS, check flags file")

                os.chdir(data.maindir)

            def multicoord(d):
                os.chdir(d)

                try:
                    data.do_concoord(d)
                    
                except FileNotFoundError:
                    raise Exception("Your CONCOORD run failed!") 

                os.chdir(data.maindir)

            pool.map(multimini, data.wds)
            pool.close()
            pool.join()

            data.update_structs()

            pool.map(multicoord, data.wds)
            pool.close()
            pool.join()

            data.wds = [d+'/'+str(i) for d in data.wds \
                    for i in range(1, len(data)+1)]

            pool.map(multienergy, data.wds)
            pool.close()
            pool.join()

            pool.map(multienergy_chains, data.wds)
            pool.close()
            pool.join()

            data.area()
        
        search = AffinityCollector(data)
        search.search_data()

        search.daffinity()
        search.ddaffinity()

        print("G values:")
        print("bound")
        print(search.G_bound_mean)
        search.G_bound_mean.to_csv('G_bound.csv')
        search.G_bound_mean.to_csv("G_bound_mean.csv")
        print("unbound")
        print(search.G_grp1_mean)
        search.G_grp1.to_csv('G_grp1.csv')
        search.G_grp1_mean.to_csv('G_grp1_mean.csv')
        print(search.G_grp2_mean)
        search.G_grp2.to_csv('G_grp2.csv')
        search.G_grp2_mean.to_csv('G_grp2_mean.csv')

        print("dG values:")
        print("bound")
        print(search.dG_bound)
        search.dG_bound.to_csv('dG_bound.csv')
        print("unbound")
        print(search.dG_unbound)
        search.dG_bound.to_csv('dG_unbound.csv')

        print("ddG values:")
        print("without fit")
        print(search.ddG)
        search.ddG.to_csv('ddG.csv')

        search.fitaffinity(**parameters)
        print("with fit:")
        print(search.ddG)
        search.ddG.to_csv('ddG_fit.csv')


data = main()

if 'stability' in data.types:

    def multienergy(d):
        os.chdir(d)
        data.do_minimization(d)
        data.single_point()
        data.electrostatics()
        data.lj()
        data.area()
        os.chdir(data.maindir)


    if 'concoord' in data.types:
        pass

    else:
        print('doing this')
        pool.map(multienergy, data.wds)
        pool.close()
        pool.join()

        search = DataCollector(data)
        search.search_lj()
        search.search_electro()
        search.search_area()

        for c in search.G.columns:
            
            for i in search.G_mean.index:
                search.G_mean.loc[i, c] = search.G.loc[i, c].mean()

#    search = DataCollector(data)
#    search.search_data()
#
    print("G values:")
    print(search.G_mean)
    search.G.to_csv("G.csv")
    search.G_mean.to_csv("G_mean.csv")

    if cliargs.fit_parameters == pkgpath:
        fitprm = pkgpath + '/parameters/fit_stability.txt'
        cliargs.fit_parameters = fitprm

    with open(cliargs.fit_parameters, 'r') as fit:
        parameters = fit.readlines()
        parameters = [l[:-1] for l in parameters] # Remove newlines
        parameters = [l.split("=") for l in parameters]
        parameters = dict([(l[0], float(l[1])) for l in parameters])


    search.dstability(gxg_table)
    print("dG folded values:")
    print(search.dG)
    print("dG unfolded values (GXG):")

    if cliargs.no_concoord:
        search.dG_unfld['-TS'] = 0

    print(search.dG_unfld)
    search.dG.to_csv("dG_fold.csv")
    search.dG_unfld.to_csv("dG_unfold.csv")

    search.ddstability()
    print("ddG values:")
    print("without fit:")
    print(search.ddG)
    search.ddG.to_csv("ddG.csv")
    ddG_fit = search.fitstability(**parameters)
    print("with fit:")
    print(ddG_fit)
    search.ddG.to_csv("ddG_fit.csv")
