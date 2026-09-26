# Hub image for the car. The base is a snapshot of Yahboom's `x3` container
# (made by car/install_hub.sh with `docker commit`), so ROS 2 Foxy and the
# Yahboom workspace are exactly what the paper's real-robot runs used.
ARG BASE=rc-base:x3-20260926
FROM ${BASE}
RUN pip3 install --no-cache-dir tornado==6.4.2
