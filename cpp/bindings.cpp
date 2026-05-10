#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include "include/backtester.hpp"

namespace py = pybind11;

namespace microstructure {

void bind_backtester(py::module_& m) {
    py::class_<Trade>(m, "Trade")
        .def_readonly("timestamp_mu", &Trade::timestamp_mu)
        .def_readonly("order_id", &Trade::order_id)
        .def_readonly("side", &Trade::side)
        .def_readonly("quantity", &Trade::quantity)
        .def_readonly("price", &Trade::price);

    py::class_<Backtester>(m, "Backtester")
        .def(py::init<>())
        .def("add_market_data", &Backtester::add_market_data,
             py::arg("timestamp_mu"), py::arg("bid_price"), py::arg("bid_volume"),
             py::arg("ask_price"), py::arg("ask_volume"),
             "Push a new LOB snapshot to the event queue")
        .def("place_order", &Backtester::place_order,
             py::arg("timestamp_mu"), py::arg("order_id"), py::arg("side"), py::arg("quantity"),
             "Place a simulated market order (side: 1 for buy, -1 for sell)")
        .def("run", &Backtester::run, "Process all events in the queue prioritized by microsecond timestamp")
        .def("get_trades", &Backtester::get_trades, "Retrieve the final trade ledger")
        .def("get_pnl", &Backtester::get_pnl, "Calculate total PnL including mark-to-market position");
}

} // namespace microstructure
