#include <pybind11/numpy.h>
#include <pybind11/pybind11.h>

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <limits>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <vector>

namespace py = pybind11;

namespace microstructure {
void bind_backtester(py::module_& m);
}

namespace {

constexpr double kEps = 1e-8;

struct MatrixView {
    const double* data;
    py::ssize_t rows;
    py::ssize_t cols;

    double at(py::ssize_t row, py::ssize_t col) const {
        return data[row * cols + col];
    }
};

MatrixView matrix_view(const py::array_t<double, py::array::c_style | py::array::forcecast>& array,
                       const char* name) {
    const py::buffer_info info = array.request();
    if (info.ndim != 2) {
        throw std::invalid_argument(std::string(name) + " must be a 2D array");
    }
    return MatrixView{
        static_cast<const double*>(info.ptr),
        info.shape[0],
        info.shape[1],
    };
}

std::vector<std::pair<py::ssize_t, py::ssize_t>> contiguous_symbol_ranges(
    const int64_t* symbol_ids,
    py::ssize_t n) {
    std::vector<std::pair<py::ssize_t, py::ssize_t>> ranges;
    if (n == 0) {
        return ranges;
    }

    py::ssize_t start = 0;
    for (py::ssize_t index = 1; index < n; ++index) {
        if (symbol_ids[index] != symbol_ids[start]) {
            ranges.emplace_back(start, index);
            start = index;
        }
    }
    ranges.emplace_back(start, n);
    return ranges;
}

void validate_shapes(const MatrixView& bid_prices,
                     const MatrixView& bid_volumes,
                     const MatrixView& ask_prices,
                     const MatrixView& ask_volumes,
                     py::ssize_t symbol_count,
                     int depth) {
    if (depth <= 0) {
        throw std::invalid_argument("depth must be positive");
    }
    if (bid_prices.rows != bid_volumes.rows || bid_prices.rows != ask_prices.rows ||
        bid_prices.rows != ask_volumes.rows) {
        throw std::invalid_argument("price and volume arrays must have the same row count");
    }
    if (bid_prices.cols < depth || bid_volumes.cols < depth ||
        ask_prices.cols < depth || ask_volumes.cols < depth) {
        throw std::invalid_argument("price and volume arrays must have at least depth columns");
    }
    if (symbol_count != bid_prices.rows) {
        throw std::invalid_argument("symbol_ids length must match price and volume row count");
    }
}

}  // namespace

py::dict build_lob_windows(
    py::array_t<double, py::array::c_style | py::array::forcecast> bid_prices_array,
    py::array_t<double, py::array::c_style | py::array::forcecast> bid_volumes_array,
    py::array_t<double, py::array::c_style | py::array::forcecast> ask_prices_array,
    py::array_t<double, py::array::c_style | py::array::forcecast> ask_volumes_array,
    py::array_t<int64_t, py::array::c_style | py::array::forcecast> symbol_ids_array,
    int window_size,
    int horizon,
    int depth,
    int rolling_norm_window) {
    if (window_size <= 1) {
        throw std::invalid_argument("window_size must be greater than 1");
    }
    if (horizon <= 0) {
        throw std::invalid_argument("horizon must be positive");
    }

    const MatrixView bid_prices = matrix_view(bid_prices_array, "bid_prices");
    const MatrixView bid_volumes = matrix_view(bid_volumes_array, "bid_volumes");
    const MatrixView ask_prices = matrix_view(ask_prices_array, "ask_prices");
    const MatrixView ask_volumes = matrix_view(ask_volumes_array, "ask_volumes");
    const py::buffer_info symbol_info = symbol_ids_array.request();
    if (symbol_info.ndim != 1) {
        throw std::invalid_argument("symbol_ids must be a 1D array");
    }
    const auto* symbol_ids = static_cast<const int64_t*>(symbol_info.ptr);
    validate_shapes(bid_prices, bid_volumes, ask_prices, ask_volumes, symbol_info.shape[0], depth);

    const py::ssize_t n = bid_prices.rows;
    if (n < window_size + horizon) {
        throw std::invalid_argument("not enough snapshots for requested window and horizon");
    }

    const py::ssize_t feature_count = static_cast<py::ssize_t>(4 * depth);
    std::vector<double> raw(static_cast<size_t>(n * feature_count), 0.0);
    std::vector<double> normalized(static_cast<size_t>(n * feature_count), 0.0);
    std::vector<double> weighted_mids(static_cast<size_t>(n), 0.0);

    {
        py::gil_scoped_release release;

        for (py::ssize_t row = 0; row < n; ++row) {
            double total_volume = 0.0;
            double total_notional = 0.0;
            for (int level = 0; level < depth; ++level) {
                const double bid_volume = std::max(0.0, bid_volumes.at(row, level));
                const double ask_volume = std::max(0.0, ask_volumes.at(row, level));
                if (std::isfinite(bid_prices.at(row, level)) && bid_prices.at(row, level) > 0.0) {
                    total_volume += bid_volume;
                    total_notional += bid_prices.at(row, level) * bid_volume;
                }
                if (std::isfinite(ask_prices.at(row, level)) && ask_prices.at(row, level) > 0.0) {
                    total_volume += ask_volume;
                    total_notional += ask_prices.at(row, level) * ask_volume;
                }
            }

            double reference_price = std::numeric_limits<double>::quiet_NaN();
            if (total_volume > 0.0) {
                reference_price = total_notional / total_volume;
            }
            if (!std::isfinite(reference_price) || reference_price <= 0.0) {
                const double best_bid = bid_prices.at(row, 0);
                const double best_ask = ask_prices.at(row, 0);
                if (std::isfinite(best_bid) && std::isfinite(best_ask) && best_bid > 0.0 && best_ask > 0.0) {
                    reference_price = (best_bid + best_ask) / 2.0;
                }
            }
            if (!std::isfinite(reference_price) || reference_price <= 0.0) {
                throw std::invalid_argument("snapshot has no valid reference price");
            }
            weighted_mids[static_cast<size_t>(row)] = reference_price;

            const py::ssize_t offset = row * feature_count;
            for (int level = 0; level < depth; ++level) {
                const double bid_price = bid_prices.at(row, level);
                const double bid_volume = std::max(0.0, bid_volumes.at(row, level));
                const double ask_price = ask_prices.at(row, level);
                const double ask_volume = std::max(0.0, ask_volumes.at(row, level));

                raw[static_cast<size_t>(offset + level)] =
                    (std::isfinite(bid_price) && bid_price > 0.0) ? (bid_price / reference_price) - 1.0 : 0.0;
                raw[static_cast<size_t>(offset + depth + level)] = std::log1p(bid_volume);
                raw[static_cast<size_t>(offset + (2 * depth) + level)] =
                    (std::isfinite(ask_price) && ask_price > 0.0) ? (ask_price / reference_price) - 1.0 : 0.0;
                raw[static_cast<size_t>(offset + (3 * depth) + level)] = std::log1p(ask_volume);
            }
        }

        for (const auto& [start, end] : contiguous_symbol_ranges(symbol_ids, n)) {
            for (py::ssize_t row = start; row < end; ++row) {
                const py::ssize_t norm_start =
                    rolling_norm_window > 0
                        ? std::max(start, row - static_cast<py::ssize_t>(rolling_norm_window) + 1)
                        : start;
                const double count = static_cast<double>(row - norm_start + 1);
                for (py::ssize_t feature = 0; feature < feature_count; ++feature) {
                    double sum = 0.0;
                    double sum_sq = 0.0;
                    for (py::ssize_t history = norm_start; history <= row; ++history) {
                        const double value = raw[static_cast<size_t>(history * feature_count + feature)];
                        sum += value;
                        sum_sq += value * value;
                    }
                    const double mean = sum / count;
                    const double variance = std::max(0.0, (sum_sq / count) - (mean * mean));
                    const double stddev = std::sqrt(variance);
                    const double value = raw[static_cast<size_t>(row * feature_count + feature)];
                    normalized[static_cast<size_t>(row * feature_count + feature)] =
                        stddev > kEps ? (value - mean) / stddev : 0.0;
                }
            }
        }
    }

    py::ssize_t sample_count = 0;
    const auto ranges = contiguous_symbol_ranges(symbol_ids, n);
    for (const auto& [start, end] : ranges) {
        const py::ssize_t length = end - start;
        if (length >= window_size + horizon) {
            sample_count += length - window_size - horizon + 1;
        }
    }
    if (sample_count <= 0) {
        throw std::invalid_argument("no per-symbol windows available for requested window and horizon");
    }

    py::array_t<float> windows(
        std::vector<py::ssize_t>{sample_count, static_cast<py::ssize_t>(window_size), feature_count});
    py::array_t<float> targets(std::vector<py::ssize_t>{sample_count});
    auto windows_info = windows.mutable_unchecked<3>();
    auto targets_info = targets.mutable_unchecked<1>();

    {
        py::gil_scoped_release release;

        py::ssize_t sample = 0;
        for (const auto& [start, end] : ranges) {
            const py::ssize_t length = end - start;
            if (length < window_size + horizon) {
                continue;
            }
            const py::ssize_t last_start = length - window_size - horizon;
            for (py::ssize_t local_start = 0; local_start <= last_start; ++local_start) {
                const py::ssize_t global_start = start + local_start;
                const py::ssize_t target_index = global_start + window_size - 1;
                const double current = weighted_mids[static_cast<size_t>(target_index)];
                const double future = weighted_mids[static_cast<size_t>(target_index + horizon)];
                targets_info(sample) = current != 0.0 && std::isfinite(current) && std::isfinite(future)
                                           ? static_cast<float>((future / current) - 1.0)
                                           : 0.0F;

                for (int window = 0; window < window_size; ++window) {
                    const py::ssize_t source_row = global_start + window;
                    for (py::ssize_t feature = 0; feature < feature_count; ++feature) {
                        const double value = normalized[static_cast<size_t>(source_row * feature_count + feature)];
                        windows_info(sample, window, feature) =
                            std::isfinite(value) ? static_cast<float>(value) : 0.0F;
                    }
                }
                ++sample;
            }
        }
    }

    py::dict result;
    result["windows"] = std::move(windows);
    result["targets"] = std::move(targets);
    return result;
}

PYBIND11_MODULE(_cpp_lob, module) {
    module.doc() = "C++ LOB preprocessing backend for microstructure research";
    module.def(
        "build_lob_windows",
        &build_lob_windows,
        py::arg("bid_prices"),
        py::arg("bid_volumes"),
        py::arg("ask_prices"),
        py::arg("ask_volumes"),
        py::arg("symbol_ids"),
        py::arg("window_size"),
        py::arg("horizon"),
        py::arg("depth") = 10,
        py::arg("rolling_norm_window") = 256,
        "Build normalized rolling LOB windows and future-return targets.");

    microstructure::bind_backtester(module);
}
