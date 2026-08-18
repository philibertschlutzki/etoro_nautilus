#!/bin/bash

# Directory Change
cd ~/etoro_nautilus || exit 1

# Environment Activation
source venv/bin/activate

# Sequential Sweep Execution
#python -m automation.optimizer.sweep --strategies all --tier all --symbols PLTR.ETORO
#python -m automation.optimizer.sweep --strategies all --tier all --symbols NVDA.ETORO 
#python -m automation.optimizer.sweep --strategies all --tier all --symbols ASML.ETORO
#python -m automation.optimizer.sweep --strategies all --tier all --symbols KRYS.ETORO
#python -m automation.optimizer.sweep --strategies all --tier all --symbols LULU.ETORO
#python -m automation.optimizer.sweep --strategies all --tier all --symbols GOOGL.ETORO
python -m automation.optimizer.sweep --strategies all --tier all --symbols TSLA.ETORO
python -m automation.optimizer.sweep --strategies all --tier all --symbols TSLA.ETORO 
#python -m automation.optimizer.sweep --strategies all --tier all --symbols NATGAS.ETORO