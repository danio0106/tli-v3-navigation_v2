#include <pybind11/pybind11.h>

namespace py = pybind11;

static py::dict get_runtime_info() {
    py::dict d;
    d["module"] = "tli_native";
    d["status"] = "stub";
    d["scanner"] = "not_implemented";
    d["overlay_worker"] = "not_implemented";
    return d;
}

static py::object create_scanner(py::object /*memory*/, py::object /*addresses*/, py::object /*progress_callback*/) {
    // Scanner implementation is intentionally deferred to v5.70.0 shadow mode.
    return py::none();
}

PYBIND11_MODULE(tli_native, m) {
    m.doc() = "Torchlight Infinite optional native runtime module (stub)";
    m.def("get_runtime_info", &get_runtime_info, "Return native runtime stub metadata");
    m.def("create_scanner", &create_scanner, "Create native scanner instance (stub)");
}
